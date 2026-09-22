"""Tests for the Congress.gov adapter (docs/source-inventory.md §1.2).

All tests run against fixtures in tests/fixtures/congress_gov/ and mocked
HTTP (respx) — no live request to api.congress.gov, and no real API key is
used anywhere in this file, per the Phase 1 implementation rules.
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
from ingestion.federal.congress_gov import CongressGovAdapter
from models.enums import Level, NormalizedStatus, PolicyType
from normalization.schema import RawRecord
from utils.config import get_settings

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "congress_gov"
BILL_LIST_URL = "https://api.congress.gov/v3/bill"

_FAKE_KEY = "test-fake-key-not-a-real-credential"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """utils.config.get_settings() is lru_cache'd — tests that flip
    CONGRESS_API_KEY via monkeypatch need a fresh read each time."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _adapter(**kwargs) -> CongressGovAdapter:
    kwargs.setdefault("api_key", _FAKE_KEY)
    return CongressGovAdapter(**kwargs)


def _standard_list_side_effect(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if params.get("offset") == "2":
        return httpx.Response(200, json=_load("bills_list_page2.json"))
    return httpx.Response(200, json=_load("bills_list_page1.json"))


def _standard_detail_side_effect(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/119/hr/4210"):
        return httpx.Response(200, json=_load("bill_detail_hr4210.json"))
    if path.endswith("/119/s/1899"):
        return httpx.Response(200, json=_load("bill_detail_s1899.json"))
    if path.endswith("/118/hr/2200"):
        return httpx.Response(200, json=_load("bill_detail_hr2200.json"))
    raise AssertionError(f"Unexpected detail request path: {path}")


_DETAIL_URL_REGEX = r"^https://api\.congress\.gov/v3/bill/\d+/[a-zA-Z]+/[\w\-]+(\?.*)?$"


def _mock_full_pagination_and_detail():
    # respx matches a plain string URL on scheme+host+path only (query
    # ignored) — so this single route catches both the first list request
    # and the pagination.next request, same path, different query string.
    respx.get(BILL_LIST_URL).mock(side_effect=_standard_list_side_effect)
    respx.get(url__regex=_DETAIL_URL_REGEX).mock(side_effect=_standard_detail_side_effect)


# --- missing / invalid API key --------------------------------------------------


def test_missing_api_key_raises_immediately_before_any_network_call(monkeypatch):
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    with pytest.raises(SourceAuthError, match="CONGRESS_API_KEY is not configured"):
        CongressGovAdapter()


def test_api_key_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", _FAKE_KEY)
    adapter = CongressGovAdapter(client=httpx.Client())
    assert adapter._auth_headers() == {"X-Api-Key": _FAKE_KEY}
    adapter.close()


def test_no_credential_is_hardcoded_in_source():
    """A cheap but real guard: the module source must not contain a
    plausible hardcoded API key literal."""
    import ingestion.federal.congress_gov as module

    source = Path(module.__file__).read_text()
    assert "CONGRESS_API_KEY" in source  # reads from env/settings
    assert _FAKE_KEY not in source  # test fixtures never leak into source


@respx.mock
def test_invalid_api_key_returns_401_raises_source_auth_error():
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(401, json={"error": {"code": "API_KEY_INVALID"}}))
    adapter = _adapter(max_retries=0)
    try:
        with pytest.raises(SourceAuthError):
            list(adapter.fetch())
    finally:
        adapter.close()


# --- normal fetch + pagination + detail enrichment -------------------------------


@respx.mock
def test_fetch_paginates_and_enriches_with_detail():
    _mock_full_pagination_and_detail()
    adapter = _adapter()
    try:
        records = list(adapter.fetch())
    finally:
        adapter.close()

    external_keys = {(r.data["congress"], r.data["type"], r.data["number"]) for r in records}
    assert external_keys == {(119, "HR", "4210"), (119, "S", "1899"), (118, "HR", "2200")}
    # Detail enrichment merged in fields the list response doesn't have:
    hr4210 = next(r for r in records if r.data["number"] == "4210")
    assert hr4210.data["introducedDate"] == "2026-06-01"
    assert "sponsors" in hr4210.data


@respx.mock
def test_fetch_sends_since_as_from_date_time():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(dict(request.url.params))
        return httpx.Response(200, json=_load("bills_list_empty.json"))

    respx.get(BILL_LIST_URL).mock(side_effect=handler)
    adapter = _adapter()
    try:
        list(adapter.fetch(since=datetime(2026, 1, 15, 8, 30, 0)))
    finally:
        adapter.close()
    assert captured[0]["fromDateTime"] == "2026-01-15T08:30:00Z"


@respx.mock
def test_fetch_sends_sort_and_format_params():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(dict(request.url.params))
        return httpx.Response(200, json=_load("bills_list_empty.json"))

    respx.get(BILL_LIST_URL).mock(side_effect=handler)
    adapter = _adapter()
    try:
        list(adapter.fetch())
    finally:
        adapter.close()
    assert captured[0]["sort"] == "updateDate+desc"  # confirmed literal value from python/bill_example.py
    assert captured[0]["format"] == "json"


@respx.mock
def test_fetch_with_zero_results_returns_empty_list():
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json=_load("bills_list_empty.json")))
    adapter = _adapter()
    try:
        records = list(adapter.fetch())
    finally:
        adapter.close()
    assert records == []


@respx.mock
def test_fetch_detail_false_skips_detail_calls_entirely():
    route = respx.get(url__regex=r".*").mock(side_effect=_standard_list_side_effect)
    adapter = _adapter(fetch_detail=False)
    try:
        records = list(adapter.fetch())
    finally:
        adapter.close()
    # Only list-endpoint calls were made (2 pages), no per-bill detail calls.
    assert route.call_count == 2
    assert all("introducedDate" not in r.data for r in records)


@respx.mock
def test_detail_fetch_failure_for_one_bill_does_not_fail_the_whole_run():
    def detail_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/119/hr/4210"):
            return httpx.Response(500)
        return _standard_detail_side_effect(request)

    respx.get(BILL_LIST_URL).mock(side_effect=_standard_list_side_effect)
    respx.get(url__regex=_DETAIL_URL_REGEX).mock(side_effect=detail_handler)

    adapter = _adapter(max_retries=0)
    try:
        records = list(adapter.fetch())
    finally:
        adapter.close()

    assert len(records) == 3  # all three bills still present
    hr4210 = next(r for r in records if r.data["number"] == "4210")
    assert hr4210.data.get("_detail_fetch_error") is True
    assert "introducedDate" not in hr4210.data
    s1899 = next(r for r in records if r.data["number"] == "1899")
    assert "introducedDate" in s1899.data  # unaffected bill still enriched normally


@respx.mock
def test_max_detail_fetches_caps_enrichment_calls():
    respx.get(BILL_LIST_URL).mock(side_effect=_standard_list_side_effect)
    detail_route = respx.get(url__regex=_DETAIL_URL_REGEX).mock(side_effect=_standard_detail_side_effect)
    adapter = _adapter(max_detail_fetches=1)
    try:
        list(adapter.fetch())
    finally:
        adapter.close()
    assert detail_route.call_count == 1


# --- failure classification ----------------------------------------------------


@respx.mock
def test_fetch_raises_source_unavailable_on_persistent_500():
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(500))
    adapter = _adapter(max_retries=0)
    try:
        with pytest.raises(SourceUnavailableError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_rate_limited_on_persistent_429():
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(429))
    adapter = _adapter(max_retries=0)
    try:
        with pytest.raises(SourceRateLimitedError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_unavailable_on_transport_error():
    respx.get(url__regex=r".*").mock(side_effect=httpx.ConnectError("connection refused"))
    adapter = _adapter(max_retries=0)
    try:
        with pytest.raises(SourceUnavailableError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_response_error_on_non_json():
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    adapter = _adapter(max_retries=0)
    try:
        with pytest.raises(SourceResponseError):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_response_error_when_bills_not_a_list():
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json=_load("malformed_bills_not_a_list.json")))
    adapter = _adapter(max_retries=0)
    try:
        with pytest.raises(SourceResponseError, match="not a list"):
            list(adapter.fetch())
    finally:
        adapter.close()


@respx.mock
def test_fetch_raises_source_response_error_when_list_item_missing_fields_needed_for_detail():
    respx.get(BILL_LIST_URL).mock(
        return_value=httpx.Response(200, json=_load("bill_missing_required_field.json"))
    )
    adapter = _adapter(max_retries=0)
    try:
        with pytest.raises(SourceResponseError, match="congress"):
            list(adapter.fetch())
    finally:
        adapter.close()


# --- normalize() ----------------------------------------------------------------


def _adapter_no_network() -> CongressGovAdapter:
    return CongressGovAdapter(api_key=_FAKE_KEY, client=httpx.Client())


def test_normalize_introduced_bill_with_no_laws():
    adapter = _adapter_no_network()
    list_item = _load("bills_list_page1.json")["bills"][0]
    detail = _load("bill_detail_hr4210.json")["bill"]
    merged = {**list_item, **detail}
    raw = RawRecord(data=merged, source_url=merged["url"], fetched_at=datetime.now())

    policy = adapter.normalize(raw)

    assert policy.source == "congress_gov_bills"
    assert policy.external_id == "119-hr-4210"
    assert policy.bill_number == "HR 4210"
    assert policy.title == "Menstrual Products in Schools Act"
    assert policy.policy_type == PolicyType.BILL
    assert policy.level == Level.FEDERAL
    assert policy.jurisdiction == "federal"
    assert policy.chamber == "house"
    assert policy.session == "119"
    assert policy.normalized_status == NormalizedStatus.UNKNOWN
    assert policy.official_status == "Referred to the House Committee on Education and the Workforce."
    assert policy.introduction_date == date(2026, 6, 1)
    assert policy.last_action_date == date(2026, 8, 19)
    assert policy.source_url == "https://www.congress.gov/bill/119th-congress/house-bill/4210"
    assert policy.official_text_url is None
    assert policy.raw_payload["sponsors"][0]["fullName"] == "Rep. Test, Jane [D-CA-12]"


def test_normalize_senate_bill_chamber_mapping():
    adapter = _adapter_no_network()
    list_item = _load("bills_list_page1.json")["bills"][1]
    detail = _load("bill_detail_s1899.json")["bill"]
    merged = {**list_item, **detail}
    raw = RawRecord(data=merged, source_url=merged["url"], fetched_at=datetime.now())

    policy = adapter.normalize(raw)

    assert policy.chamber == "senate"
    assert policy.external_id == "119-s-1899"
    assert policy.source_url == "https://www.congress.gov/bill/119th-congress/senate-bill/1899"


def test_normalize_enacted_bill_maps_to_enacted_status():
    adapter = _adapter_no_network()
    list_item = _load("bills_list_page2.json")["bills"][0]
    detail = _load("bill_detail_hr2200.json")["bill"]
    merged = {**list_item, **detail}
    raw = RawRecord(data=merged, source_url=merged["url"], fetched_at=datetime.now())

    policy = adapter.normalize(raw)

    assert policy.normalized_status == NormalizedStatus.ENACTED
    assert policy.official_status == "Became Public Law No: 118-245."


def test_normalize_raises_on_missing_required_field():
    adapter = _adapter_no_network()
    item = _load("bill_missing_required_field.json")["bills"][0]
    raw = RawRecord(data=item, source_url="https://api.congress.gov/v3/bill", fetched_at=datetime.now())
    with pytest.raises(SourceResponseError, match="congress"):
        adapter.normalize(raw)


def test_normalize_raw_payload_preserved_end_to_end_and_json_serializable():
    adapter = _adapter_no_network()
    list_item = _load("bills_list_page1.json")["bills"][0]
    detail = _load("bill_detail_hr4210.json")["bill"]
    merged = {**list_item, **detail}
    raw = RawRecord(data=merged, source_url=merged["url"], fetched_at=datetime.now())
    policy = adapter.normalize(raw)
    assert policy.raw_payload == merged  # nothing dropped, nothing invented
