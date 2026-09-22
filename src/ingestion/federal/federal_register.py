"""Federal Register API adapter — rules, proposed rules, and executive orders.

architecture.md §3.3 tier 1 (aggregator-backed, no API key required). One
source (`federal_register`) covering two policy_types
(`regulation`, `executive_order`), matching a single source_registry row.

Field names, the `type` filter's accepted values, and the search-response
envelope were verified against the API's own public backend source
(github.com/usnationalarchives/federalregister-api-core) because this
environment's egress policy blocks direct requests to federalregister.gov —
see docs/source-inventory.md §1.1 for the full verification method and the
discrepancies found against the original research pass (most notably: the
field is `effective_on`, not `effective_date`; a zero-result response omits
the `results` key entirely rather than returning an empty list).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

import httpx

from ingestion.base.errors import (
    SourceResponseError,
    classify_http_error,
    classify_transport_error,
)
from ingestion.base.rest_api import RestApiAdapter
from models.enums import Level, NormalizedStatus, PolicyType
from normalization.schema import PolicyIn, RawRecord

_DOCUMENTS_ENDPOINT = "/api/v1/documents.json"

# ENTRY_TYPES filter codes, verified against app/searches/entry_search.rb and
# app/models/entry.rb (ENTRY_TYPES constant) — see source-inventory.md §1.1.
_REGULATION_TYPE_CODES = ["RULE", "PRORULE"]
_PRESIDENTIAL_DOCUMENT_TYPE_CODE = "PRESDOCU"

# The `type` field's actual string values, verified from the live ENTRY_TYPES
# constant (not guessed) — these are what the JSON response contains, not
# the filter-condition codes above.
_TYPE_RULE = "Rule"
_TYPE_PRORULE = "Proposed Rule"
_TYPE_PRESIDENTIAL_DOCUMENT = "Presidential Document"

# subtype is the presidential_document_type's display name. The exact
# *filter* value for `conditions[presidential_document_type]` could not be
# confirmed from source in the time available, so this adapter fetches all
# Presidential Documents and filters on this verified field instead of an
# unverified filter parameter — see source-inventory.md §1.1 discrepancy 3.
_SUBTYPE_EXECUTIVE_ORDER = "executive order"

# Fields requested per document — a subset of entry_api_representation.rb's
# full field list, limited to what PolicyIn actually uses. Requesting fewer
# fields than the default index set is supported by the API's `fields[]`
# parameter (confirmed via ApiController#specified_fields).
_REQUESTED_FIELDS = [
    "document_number",
    "title",
    "abstract",
    "type",
    "subtype",
    "html_url",
    "pdf_url",
    "publication_date",
    "signing_date",
    "effective_on",
    "executive_order_number",
    "citation",
]

# Not independently confirmed against source (source-inventory.md §1.1
# discrepancy 5) — a third-party client library documents 1000 as the
# maximum; kept conservative until a live smoke test confirms it.
_DEFAULT_PER_PAGE = 100
_DEFAULT_MAX_PAGES = 20


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class FederalRegisterAdapter(RestApiAdapter):
    base_url = "https://www.federalregister.gov"
    source_id = "federal_register"
    jurisdiction = "federal"

    def __init__(self, *, per_page: int = _DEFAULT_PER_PAGE, max_pages: int = _DEFAULT_MAX_PAGES, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._per_page = per_page
        self._max_pages = max_pages

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        records: list[RawRecord] = []

        for item in self._fetch_all_items(_REGULATION_TYPE_CODES, since):
            records.append(self._raw_record_from_item(item))

        for item in self._fetch_all_items([_PRESIDENTIAL_DOCUMENT_TYPE_CODE], since):
            subtype = (item.get("subtype") or "").strip().lower()
            if subtype == _SUBTYPE_EXECUTIVE_ORDER:
                records.append(self._raw_record_from_item(item))
            # Non-EO presidential documents (Proclamations, Memoranda,
            # Determinations, Notices) are in scope for the API's PRESDOCU
            # type but out of scope for this adapter/milestone — see
            # architecture.md §3.3 ("Federal Register for rules and
            # executive orders"). Deliberately dropped here, not an error.

        return records

    def _base_params(self, type_codes: list[str], since: datetime | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "per_page": self._per_page,
            "order": "newest",
            "conditions[type][]": type_codes,
            "fields[]": _REQUESTED_FIELDS,
        }
        if since is not None:
            since_date = since.date() if isinstance(since, datetime) else since
            params["conditions[publication_date][gte]"] = since_date.isoformat()
        return params

    def _fetch_all_items(self, type_codes: list[str], since: datetime | None) -> list[dict[str, Any]]:
        """Paginates via next_page_url (confirmed field — source-inventory.md
        §1.1) until exhausted or `_max_pages` is reached, a safety guard
        against an unbounded fetch (not itself a documented API limit)."""
        items: list[dict[str, Any]] = []
        payload: dict[str, Any] | None = self._safe_get_json(_DOCUMENTS_ENDPOINT, self._base_params(type_codes, since))
        pages_fetched = 0
        while payload is not None:
            pages_fetched += 1
            items.extend(self._extract_results(payload))
            next_page_url = payload.get("next_page_url")
            if not next_page_url or pages_fetched >= self._max_pages:
                break
            payload = self._safe_get_json(next_page_url, params=None)
        return items

    def _safe_get_json(self, path: str, params: dict[str, Any] | None) -> dict[str, Any]:
        try:
            payload = self._get_json(path, params=params)
        except httpx.HTTPStatusError as exc:
            raise classify_http_error(exc) from exc
        except httpx.TransportError as exc:
            raise classify_transport_error(exc) from exc
        except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
            raise SourceResponseError(f"Federal Register returned non-JSON response: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceResponseError(f"Federal Register response was not a JSON object (got {type(payload).__name__})")
        return payload

    @staticmethod
    def _extract_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        # A legitimate zero-result search omits "results" entirely rather
        # than returning [] — confirmed in api_controller.rb's render_search
        # (source-inventory.md §1.1 discrepancy 2). This must not raise.
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise SourceResponseError(f"Federal Register 'results' was not a list (got {type(results).__name__})")
        return results

    def _raw_record_from_item(self, item: dict[str, Any]) -> RawRecord:
        source_url = item.get("html_url") or f"{self.base_url}{_DOCUMENTS_ENDPOINT}"
        return RawRecord(data=item, source_url=source_url, fetched_at=datetime.now())

    def normalize(self, raw: RawRecord) -> PolicyIn:
        data = raw.data
        try:
            document_number = data["document_number"]
            title = data["title"]
            doc_type = data["type"]
        except KeyError as exc:
            raise SourceResponseError(f"Federal Register record missing required field {exc}") from exc

        subtype = data.get("subtype")

        if doc_type == _TYPE_PRESIDENTIAL_DOCUMENT and (subtype or "").strip().lower() == _SUBTYPE_EXECUTIVE_ORDER:
            policy_type = PolicyType.EXECUTIVE_ORDER
            normalized_status = NormalizedStatus.EFFECTIVE
            effective_date = _parse_date(data.get("signing_date")) or _parse_date(data.get("publication_date"))
            official_status = f"{doc_type} ({subtype})"
        elif doc_type == _TYPE_RULE:
            policy_type = PolicyType.REGULATION
            normalized_status = NormalizedStatus.ADOPTED
            effective_date = _parse_date(data.get("effective_on"))
            official_status = doc_type
        elif doc_type == _TYPE_PRORULE:
            policy_type = PolicyType.REGULATION
            normalized_status = NormalizedStatus.PROPOSED
            effective_date = None
            official_status = doc_type
        else:
            # fetch() only ever requests RULE/PRORULE/PRESDOCU, and
            # non-EO presidential documents are filtered out before
            # normalize() is called — so reaching here means the API
            # returned something outside what this adapter was verified
            # against. Raise rather than guess at a mapping.
            raise SourceResponseError(
                f"Unexpected Federal Register type/subtype combination: "
                f"type={doc_type!r} subtype={subtype!r} (document_number={document_number!r})"
            )

        return PolicyIn(
            source=self.source_id,
            external_id=document_number,
            title=title,
            policy_type=policy_type,
            jurisdiction="federal",
            level=Level.FEDERAL,
            description=data.get("abstract"),
            official_status=official_status,
            normalized_status=normalized_status,
            last_action_date=_parse_date(data.get("publication_date")),
            effective_date=effective_date,
            source_url=data.get("html_url") or raw.source_url,
            official_text_url=data.get("pdf_url"),
            raw_payload=data,
        )
