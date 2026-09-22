"""Tests for the Federal Register adapter (docs/source-inventory.md §1.1).

All tests run against fixtures in tests/fixtures/federal_register/ and mocked
HTTP (respx) — no live request to federalregister.gov, per the Phase 1
implementation rules.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from ingestion.base.errors import (
    SourceAuthError,
    SourceRateLimitedError,
    SourceResponseError,
    SourceUnavailableError,
)
from ingestion.federal.federal_register import FederalRegisterAdapter
from models.enums import Level, NormalizedStatus, PolicyType
from normalization.schema import RawRecord

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "federal_register"
DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _standard_side_effect(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if params.get("page") == "2":
        return httpx.Response(200, json=_load("regulations_page2.json"))
    type_codes = params.get_list("conditions[type][]")
    if type_codes == ["RULE", "PRORULE"]:
        return httpx.Response(200, json=_load("regulations_page1.json"))
    if type_codes == ["PRESDOCU"]:
        return httpx.Response(200, json=_load("presidential_documents_page1.json"))
    raise AssertionError(f"Unexpected request params: {dict(params)}")


# --- fetch() ------------------------------------------------------------------


@respx.mock
def test_fetch_returns_regulations_and_executive_order_only():
    respx.get(DOCUMENTS_URL).mock(side_effect=_standard_side_effect)
    adapter = FederalRegisterAdapter()
    try:
        records = list(adapter.fetch())
    finally:
        adapter.close()

    document_numbers = {r.data["document_number"] for r in records}
    # Two regulations (across two pages) + one executive order. The
    # proclamation in presidential_documents_page1.json must be filtered out.
    assert document_numbers == {"2026-14822", "2026-15990", "2026-16500"}


@respx.mock
def test_fetch_follows_pagination_via_next_page_url():
    route = respx.get(DOCUMENTS_URL).mock(side_effect=_standard_side_effect)
    adapter = FederalRegisterAdapter()
    try:
        list(adapter.fetch())
    finally:
        adapter.close()
    # 1 page for regulations type-group (which itself follows one next_page_url
    # to a 2nd page) + 1 page for the presidential-documents type-group = 3 calls.
    assert route.call_count == 3


@respx.mock
def test_fetch_sends_since_as_publication_date_gte():
    captured_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.append(dict(request.url.params))
        return httpx.Response(200, json=_load("empty_response.json"))

    respx.get(DOCUMENTS_URL).mock(side_effect=handler)
    adapter = FederalRegisterAdapter()
    try:
        list(adapter.fetch(since=datetime(2026, 1, 15)))
    finally:
        adapter.close()

    assert all(p["conditions[publication_date][gte]"] == "2026-01-15" for p in captured_params)


@respx.mock
def test_fetch_with_zero_results_returns_empty_list_without_error():
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(200, json=_load("empty_response.json")))
    adapter = FederalRegisterAdapter()
    try:
        records = list(adapter.fetch())
    finally:
        adapter.close()
    assert records == []


@respx.mock
def test_fetch_respects_max_pages_safety_cap():
    """A pathological always-has-next-page response must not loop forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _load("regulations_page1.json")
        payload["next_page_url"] = str(request.url)  # always claims another page
        return httpx.Response(200, json=payload)

    route = respx.get(DOCUMENTS_URL).mock(side_effect=handler)
    adapter = FederalRegisterAdapter(max_pages=3)
    try:
        list(adapter.fetch())
    finally:
        adapter.close()
    # One type-group call for regulations, one for presidential documents;
    # each must stop at exactly max_pages despite next_page_url always
    # being present, rather than looping forever.
    assert route.call_count == 2 * 3


# --- failure classification ----------------------------------------------------


@respx.mock
def test_fetch_raises_source_unavailable_on_persistent_500():
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(500))
    adapter = FederalRegisterAdapter(max_retries=0)
    try:
        with pytest.raises(SourceUnavailableError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_rate_limited_on_persistent_429():
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(429))
    adapter = FederalRegisterAdapter(max_retries=0)
    try:
        with pytest.raises(SourceRateLimitedError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_auth_error_on_401():
    """Federal Register requires no auth, so this shouldn't occur in
    practice, but the generic classifier must still work correctly if the
    API ever returns it (e.g. a misrouted request)."""
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(401))
    adapter = FederalRegisterAdapter(max_retries=0)
    try:
        with pytest.raises(SourceAuthError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_unavailable_on_transport_error():
    respx.get(DOCUMENTS_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    adapter = FederalRegisterAdapter(max_retries=0)
    try:
        with pytest.raises(SourceUnavailableError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_response_error_on_non_json():
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    adapter = FederalRegisterAdapter(max_retries=0)
    try:
        with pytest.raises(SourceResponseError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_response_error_when_results_not_a_list():
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(200, json=_load("malformed_results_not_a_list.json")))
    adapter = FederalRegisterAdapter(max_retries=0)
    try:
        with pytest.raises(SourceResponseError, match="not a list"):
            list(adapter.fetch())
    finally:
        adapter.close()


# --- normalize() ----------------------------------------------------------------


def _adapter() -> FederalRegisterAdapter:
    return FederalRegisterAdapter(client=httpx.Client())  # no network calls made in these tests


def test_normalize_final_rule():
    adapter = _adapter()
    item = _load("regulations_page1.json")["results"][0]
    raw = RawRecord(data=item, source_url=item["html_url"], fetched_at=datetime.now())
    policy = adapter.normalize(raw)

    assert policy.source == "federal_register"
    assert policy.external_id == "2026-14822"
    assert policy.policy_type == PolicyType.REGULATION
    assert policy.normalized_status == NormalizedStatus.ADOPTED
    assert policy.level == Level.FEDERAL
    assert policy.jurisdiction == "federal"
    assert policy.effective_date == date(2026, 8, 9)
    assert policy.last_action_date == date(2026, 7, 10)
    assert policy.source_url == item["html_url"]
    assert policy.official_text_url == item["pdf_url"]
    assert policy.official_status == "Rule"
    assert policy.raw_payload == item


def test_normalize_proposed_rule():
    adapter = _adapter()
    item = _load("regulations_page2.json")["results"][0]
    raw = RawRecord(data=item, source_url=item["html_url"], fetched_at=datetime.now())
    policy = adapter.normalize(raw)

    assert policy.policy_type == PolicyType.REGULATION
    assert policy.normalized_status == NormalizedStatus.PROPOSED
    assert policy.effective_date is None


def test_normalize_executive_order():
    adapter = _adapter()
    item = _load("presidential_documents_page1.json")["results"][0]
    assert item["subtype"] == "Executive Order"
    raw = RawRecord(data=item, source_url=item["html_url"], fetched_at=datetime.now())
    policy = adapter.normalize(raw)

    assert policy.policy_type == PolicyType.EXECUTIVE_ORDER
    assert policy.normalized_status == NormalizedStatus.EFFECTIVE
    assert policy.effective_date == date(2026, 8, 12)  # falls back to signing_date
    assert policy.official_status == "Presidential Document (Executive Order)"
    assert "Executive Order 14400" in policy.title


def test_normalize_raises_on_missing_required_field():
    adapter = _adapter()
    item = _load("item_missing_required_field.json")["results"][0]
    raw = RawRecord(data=item, source_url=item["html_url"], fetched_at=datetime.now())
    with pytest.raises(SourceResponseError, match="document_number"):
        adapter.normalize(raw)


def test_normalize_raises_on_unexpected_type():
    """fetch() never requests NOTICE, but normalize() must fail loudly
    rather than silently mis-map it if it's ever called with one."""
    adapter = _adapter()
    item = {
        "document_number": "2026-99999",
        "title": "Some Notice",
        "type": "Notice",
        "html_url": "https://www.federalregister.gov/documents/2026/09/01/example",
        "publication_date": "2026-09-01",
    }
    raw = RawRecord(data=item, source_url=item["html_url"], fetched_at=datetime.now())
    with pytest.raises(SourceResponseError, match="Unexpected Federal Register type"):
        adapter.normalize(raw)


def test_normalize_raw_payload_is_json_serializable_end_to_end():
    """Guards against PolicyIn's own validator (normalization/schema.py)
    rejecting a real Federal Register payload."""
    adapter = _adapter()
    for fixture_name, index in [("regulations_page1.json", 0), ("presidential_documents_page1.json", 0)]:
        item = _load(fixture_name)["results"][index]
        raw = RawRecord(data=item, source_url=item["html_url"], fetched_at=datetime.now())
        policy = adapter.normalize(raw)
        assert policy.raw_payload  # constructed successfully, validator didn't reject it
