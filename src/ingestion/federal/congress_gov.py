"""Congress.gov API adapter — federal bills and resolutions.

VERIFICATION METHOD — distinct from the Federal Register adapter's, and
weaker in one respect: no live request was possible here for two
independent reasons (no CONGRESS_API_KEY configured in this environment,
and api.congress.gov is blocked by this environment's egress policy
regardless of key availability). Per the implementation rules, neither
condition was worked around and no response shape was invented. This
adapter is built against the Congress.gov API's own official repository
(github.com/LibraryOfCongress/api.congress.gov) — its documented field
lists, its reference Python client's actual request code, and a real
recorded example response — which is "official documentation/source-code
verification," not live API verification. Full detail, including every
discrepancy found against the original research pass (most notably: the
API key goes in an `X-Api-Key` *header*, not a query parameter, confirmed
directly from the reference client's source), is in
docs/source-inventory.md §1.2. A live smoke test with a real key is a
tracked pre-production action item (docs/roadmap.md §1.5) — same status as
the Federal Register adapter's.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

import httpx

from ingestion.base.errors import (
    SourceAuthError,
    SourceError,
    SourceResponseError,
    classify_http_error,
    classify_transport_error,
)
from ingestion.base.rest_api import RestApiAdapter
from models.enums import Level, NormalizedStatus, PolicyType
from normalization.schema import PolicyIn, RawRecord
from utils.config import get_settings

_BILL_ENDPOINT = "/v3/bill"

# Confirmed via README ("adjusted up to 250 results") — source-inventory.md §1.2.
_DEFAULT_LIMIT = 250
_DEFAULT_MAX_PAGES = 20
# Not a documented API limit — an implementation safety guard bounding how
# many per-bill detail calls one fetch() makes, since detail enrichment is
# N+1 or (list pages + N bills), unlike Federal Register's flat list fetch.
_DEFAULT_MAX_DETAIL_FETCHES = 500

# Congress.gov's public bill-type-to-URL-slug mapping. Well-established,
# stable public knowledge of congress.gov's own URL scheme — not verified
# against source code this session (there is no such source to check; it's
# a public URL convention, not an API behavior). See source-inventory.md
# §1.2 discrepancy 4.
_BILL_TYPE_SLUGS = {
    "hr": "house-bill",
    "s": "senate-bill",
    "hres": "house-resolution",
    "sres": "senate-resolution",
    "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution",
}

_CHAMBER_MAP = {"house": "house", "senate": "senate"}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _to_congress_datetime(value: datetime) -> str:
    """Confirmed format from python/bill_example.py: '2022-01-04T04:02:00Z'."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _congress_gov_public_url(congress: Any, bill_type: Any, number: Any) -> str | None:
    slug = _BILL_TYPE_SLUGS.get(str(bill_type).lower())
    if slug is None:
        return None
    return f"https://www.congress.gov/bill/{congress}th-congress/{slug}/{number}"


class CongressGovAdapter(RestApiAdapter):
    base_url = "https://api.congress.gov"
    source_id = "congress_gov_bills"
    jurisdiction = "federal"

    def __init__(
        self,
        *,
        limit: int = _DEFAULT_LIMIT,
        max_pages: int = _DEFAULT_MAX_PAGES,
        fetch_detail: bool = True,
        max_detail_fetches: int = _DEFAULT_MAX_DETAIL_FETCHES,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Raises SourceAuthError immediately — before any network call — if
        no API key is configured, per the requirement that this adapter fail
        clearly and safely rather than silently degrade or guess. Pass
        `api_key` explicitly only in tests; production code should rely on
        the CONGRESS_API_KEY environment variable (utils.config), never a
        hard-coded value.
        """
        resolved_key = api_key if api_key is not None else get_settings().congress_api_key
        if not resolved_key:
            raise SourceAuthError(
                "CONGRESS_API_KEY is not configured. This adapter cannot run "
                "without it — set the environment variable before using "
                "CongressGovAdapter. (The pipeline as a whole does not "
                "require any API key; this is specific to this one source.)"
            )
        self._api_key = resolved_key
        super().__init__(**kwargs)
        self._limit = limit
        self._max_pages = max_pages
        self._fetch_detail = fetch_detail
        self._max_detail_fetches = max_detail_fetches

    def _auth_headers(self) -> dict[str, str]:
        # A header, not a query param — confirmed from the official
        # reference client's source (source-inventory.md §1.2).
        return {"X-Api-Key": self._api_key}

    def _base_params(self, since: datetime | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "format": "json",
            "limit": self._limit,
            "sort": "updateDate+desc",
        }
        if since is not None:
            params["fromDateTime"] = _to_congress_datetime(since)
        return params

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        records: list[RawRecord] = []
        detail_fetches = 0

        for item in self._fetch_list(since):
            merged = dict(item)
            if self._fetch_detail and detail_fetches < self._max_detail_fetches:
                detail_fetches += 1
                # Identifying which detail URL to call is NOT wrapped in the
                # try/except below: a list item missing congress/type/number
                # means the list response itself is malformed, which must
                # propagate (it's not an isolated per-bill network hiccup).
                detail_path = self._bill_detail_path(item)
                try:
                    merged.update(self._fetch_bill_detail(detail_path))
                except SourceError:
                    # An actual HTTP/response failure calling that path,
                    # once identified, IS isolated to this one bill — must
                    # not fail the whole run. List-level fields are still
                    # valid and usable; note the gap for transparency
                    # rather than silently pretending detail was fetched.
                    merged["_detail_fetch_error"] = True
            api_url = merged.get("url")
            source_url = api_url if isinstance(api_url, str) else f"{self.base_url}{_BILL_ENDPOINT}"
            records.append(RawRecord(data=merged, source_url=source_url, fetched_at=datetime.now()))

        return records

    def _fetch_list(self, since: datetime | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        payload: dict[str, Any] | None = self._safe_get_json(_BILL_ENDPOINT, self._base_params(since))
        pages_fetched = 0
        while payload is not None:
            pages_fetched += 1
            items.extend(self._extract_bills(payload))
            next_url = (payload.get("pagination") or {}).get("next")
            if not next_url or pages_fetched >= self._max_pages:
                break
            payload = self._safe_get_json(next_url, params=None)
        return items

    @staticmethod
    def _bill_detail_path(item: dict[str, Any]) -> str:
        try:
            congress = item["congress"]
            bill_type = str(item["type"]).lower()
            number = item["number"]
        except KeyError as exc:
            raise SourceResponseError(f"Congress.gov list item missing field needed for detail fetch: {exc}") from exc
        return f"{_BILL_ENDPOINT}/{congress}/{bill_type}/{number}"

    def _fetch_bill_detail(self, path: str) -> dict[str, Any]:
        payload = self._safe_get_json(path, params={"format": "json"})
        bill = payload.get("bill")
        if not isinstance(bill, dict):
            raise SourceResponseError(f"Congress.gov bill-detail response missing 'bill' object at {path}")
        return bill

    def _safe_get_json(self, path: str, params: dict[str, Any] | None) -> dict[str, Any]:
        try:
            payload = self._get_json(path, params=params)
        except httpx.HTTPStatusError as exc:
            raise classify_http_error(exc) from exc
        except httpx.TransportError as exc:
            raise classify_transport_error(exc) from exc
        except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
            raise SourceResponseError(f"Congress.gov returned non-JSON response: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceResponseError(f"Congress.gov response was not a JSON object (got {type(payload).__name__})")
        return payload

    @staticmethod
    def _extract_bills(payload: dict[str, Any]) -> list[dict[str, Any]]:
        bills = payload.get("bills", [])
        if not isinstance(bills, list):
            raise SourceResponseError(f"Congress.gov 'bills' was not a list (got {type(bills).__name__})")
        return bills

    def normalize(self, raw: RawRecord) -> PolicyIn:
        data = raw.data
        try:
            congress = data["congress"]
            bill_type = data["type"]
            number = data["number"]
            title = data["title"]
        except KeyError as exc:
            raise SourceResponseError(f"Congress.gov bill record missing required field {exc}") from exc

        external_id = f"{congress}-{str(bill_type).lower()}-{number}"
        chamber = _CHAMBER_MAP.get(str(data.get("originChamber", "")).lower())

        laws = data.get("laws")
        normalized_status = NormalizedStatus.ENACTED if isinstance(laws, list) and laws else NormalizedStatus.UNKNOWN

        latest_action = data.get("latestAction")
        if isinstance(latest_action, dict):
            official_status = latest_action.get("text")
            last_action_date = _parse_date(latest_action.get("actionDate"))
        else:
            official_status = None
            last_action_date = _parse_date(data.get("updateDate"))

        source_url = (
            _congress_gov_public_url(congress, bill_type, number)
            or data.get("url")
            or raw.source_url
        )

        return PolicyIn(
            source=self.source_id,
            external_id=external_id,
            title=title,
            bill_number=f"{str(bill_type).upper()} {number}",
            policy_type=PolicyType.BILL,
            jurisdiction="federal",
            level=Level.FEDERAL,
            chamber=chamber,
            session=str(congress),
            official_status=official_status,
            normalized_status=normalized_status,
            introduction_date=_parse_date(data.get("introducedDate")),
            last_action_date=last_action_date,
            source_url=source_url,
            official_text_url=None,  # requires a separate textVersions call — see source-inventory.md §1.2
            raw_payload=data,
        )
