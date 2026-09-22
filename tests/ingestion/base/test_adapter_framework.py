"""Tests for the reusable adapter base classes (architecture.md §3.2).

These are deliberately generic — they prove the framework's plumbing
(HTTP + retry, HTML parsing, PDF extraction, feed parsing, rate limiting,
the fetch/normalize contract) works against fixture data, with zero
dependency on any real government source. Source-specific adapters (Federal
Register, Congress.gov, CA/WA regulations & EOs) get their own tests once
built.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from ingestion.base.adapter import SourceAdapter
from ingestion.base.html_list import HtmlListAdapter
from ingestion.base.pdf_document import PdfDocumentAdapter
from ingestion.base.playwright_adapter import PlaywrightAdapter
from ingestion.base.rest_api import RestApiAdapter
from ingestion.base.rss_feed import RssFeedAdapter
from models.enums import Level, PolicyType
from normalization.schema import PolicyIn, RawRecord
from utils.http import RateLimiter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


# --- SourceAdapter (the abstract contract) ----------------------------------


def test_source_adapter_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        SourceAdapter()  # type: ignore[abstract]


def test_incomplete_subclass_missing_normalize_cannot_be_instantiated():
    class _Incomplete(SourceAdapter):
        source_id = "incomplete"
        jurisdiction = "federal"

        def fetch(self, since=None):
            return []

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_context_manager_calls_close():
    closed = []

    class _Dummy(SourceAdapter):
        source_id = "dummy"
        jurisdiction = "federal"

        def fetch(self, since=None):
            return []

        def normalize(self, raw):
            raise NotImplementedError

        def close(self):
            closed.append(True)

    with _Dummy():
        pass

    assert closed == [True]


# --- RestApiAdapter ----------------------------------------------------------


class _DummyRestAdapter(RestApiAdapter):
    base_url = "https://example.gov"
    source_id = "dummy_rest"
    jurisdiction = "federal"

    def _auth_headers(self):
        return {"X-Test-Auth": "secret"}

    def fetch(self, since=None):
        payload = self._get_json("/items")
        return [
            RawRecord(data=item, source_url=f"https://example.gov/items/{item['id']}", fetched_at=datetime.now(timezone.utc))
            for item in payload["items"]
        ]

    def normalize(self, raw: RawRecord) -> PolicyIn:
        return PolicyIn(
            source=self.source_id,
            external_id=str(raw.data["id"]),
            title=raw.data["title"],
            policy_type=PolicyType.BILL,
            jurisdiction="federal",
            level=Level.FEDERAL,
            source_url=raw.source_url,
            raw_payload=raw.data,
        )


@respx.mock
def test_rest_api_adapter_fetch_and_normalize_round_trip():
    respx.get("https://example.gov/items").mock(
        return_value=httpx.Response(200, json={"items": [{"id": 1, "title": "A Bill"}, {"id": 2, "title": "Another Bill"}]})
    )
    adapter = _DummyRestAdapter()
    try:
        raw_records = list(adapter.fetch())
        assert len(raw_records) == 2
        policies = [adapter.normalize(r) for r in raw_records]
        assert {p.external_id for p in policies} == {"1", "2"}
        assert policies[0].raw_payload == {"id": 1, "title": "A Bill"}
    finally:
        adapter.close()


@respx.mock
def test_rest_api_adapter_sends_auth_headers():
    route = respx.get("https://example.gov/items").mock(return_value=httpx.Response(200, json={"items": []}))
    adapter = _DummyRestAdapter()
    try:
        list(adapter.fetch())
    finally:
        adapter.close()
    assert route.calls.last.request.headers["X-Test-Auth"] == "secret"


@respx.mock
def test_rest_api_adapter_respects_injected_rate_limiter():
    respx.get("https://example.gov/items").mock(return_value=httpx.Response(200, json={"items": []}))
    acquired = []
    limiter = RateLimiter(100, 60)
    limiter.acquire = lambda: acquired.append(True)  # type: ignore[method-assign]
    adapter = _DummyRestAdapter(rate_limiters=[limiter])
    try:
        list(adapter.fetch())
    finally:
        adapter.close()
    assert acquired == [True]


# --- HtmlListAdapter ----------------------------------------------------------


_SAMPLE_HTML = """
<html><body>
<ul class="notices">
  <li><a href="/notice/1">Notice 1: Menstrual Product Access in Schools</a></li>
  <li><a href="/notice/2">Notice 2: Unrelated Rule</a></li>
</ul>
</body></html>
"""


class _DummyHtmlAdapter(HtmlListAdapter):
    base_url = "https://example.gov"
    source_id = "dummy_html"
    jurisdiction = "CA"

    def fetch(self, since=None):
        soup = self._get_soup("/notices")
        records = []
        for link in soup.select("ul.notices li a"):
            records.append(
                RawRecord(
                    data={"title": link.text.strip(), "href": link["href"]},
                    source_url=f"https://example.gov{link['href']}",
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        return records

    def normalize(self, raw: RawRecord) -> PolicyIn:
        return PolicyIn(
            source=self.source_id,
            external_id=raw.data["href"],
            title=raw.data["title"],
            policy_type=PolicyType.REGULATION,
            jurisdiction="california",
            state="CA",
            level=Level.STATE,
            source_url=raw.source_url,
            raw_payload=raw.data,
        )


@respx.mock
def test_html_list_adapter_parses_listing_page():
    respx.get("https://example.gov/notices").mock(return_value=httpx.Response(200, text=_SAMPLE_HTML))
    adapter = _DummyHtmlAdapter()
    try:
        raw_records = list(adapter.fetch())
        assert len(raw_records) == 2
        policies = [adapter.normalize(r) for r in raw_records]
        assert policies[0].title == "Notice 1: Menstrual Product Access in Schools"
        assert policies[0].source_url == "https://example.gov/notice/1"
    finally:
        adapter.close()


# --- PdfDocumentAdapter --------------------------------------------------------


class _DummyPdfAdapter(PdfDocumentAdapter):
    base_url = "https://example.gov"
    source_id = "dummy_pdf"
    jurisdiction = "WA"

    def fetch(self, since=None):
        pdf_bytes = self._download_pdf("https://example.gov/order.pdf")
        text = self._extract_text(pdf_bytes)
        return [
            RawRecord(
                data={"text": text},
                source_url="https://example.gov/order.pdf",
                fetched_at=datetime.now(timezone.utc),
                raw_document_bytes=pdf_bytes,
            )
        ]

    def normalize(self, raw: RawRecord) -> PolicyIn:
        return PolicyIn(
            source=self.source_id,
            external_id="order-1",
            title=raw.data["text"][:50],
            policy_type=PolicyType.EXECUTIVE_ORDER,
            jurisdiction="washington",
            state="WA",
            level=Level.STATE,
            source_url=raw.source_url,
            official_text_url=raw.source_url,
            raw_payload={"extracted_text": raw.data["text"]},
        )


@respx.mock
def test_pdf_document_adapter_downloads_and_extracts_text():
    pdf_bytes = (FIXTURES / "sample.pdf").read_bytes()
    respx.get("https://example.gov/order.pdf").mock(return_value=httpx.Response(200, content=pdf_bytes))
    adapter = _DummyPdfAdapter()
    try:
        raw_records = list(adapter.fetch())
        assert len(raw_records) == 1
        assert "Hello PDF" in raw_records[0].data["text"]
        policy = adapter.normalize(raw_records[0])
        assert policy.official_text_url == "https://example.gov/order.pdf"
    finally:
        adapter.close()


# --- RssFeedAdapter ------------------------------------------------------------

_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example Agency Notices</title>
<item>
  <title>New Guidance on Menstrual Products in Correctional Facilities</title>
  <link>https://example.gov/guidance/1</link>
  <pubDate>Mon, 01 Sep 2026 00:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


class _DummyRssAdapter(RssFeedAdapter):
    feed_url = "https://example.gov/feed.xml"
    source_id = "dummy_rss"
    jurisdiction = "federal"

    def fetch(self, since=None):
        feed = self._get_feed()
        return [
            RawRecord(
                data={"title": entry.title, "link": entry.link},
                source_url=entry.link,
                fetched_at=datetime.now(timezone.utc),
            )
            for entry in feed.entries
        ]

    def normalize(self, raw: RawRecord) -> PolicyIn:
        return PolicyIn(
            source=self.source_id,
            external_id=raw.data["link"],
            title=raw.data["title"],
            policy_type=PolicyType.GUIDANCE,
            jurisdiction="federal",
            level=Level.FEDERAL,
            source_url=raw.source_url,
            raw_payload=raw.data,
        )


@respx.mock
def test_rss_feed_adapter_parses_feed():
    respx.get("https://example.gov/feed.xml").mock(return_value=httpx.Response(200, content=_SAMPLE_RSS.encode()))
    adapter = _DummyRssAdapter()
    try:
        raw_records = list(adapter.fetch())
        assert len(raw_records) == 1
        policy = adapter.normalize(raw_records[0])
        assert policy.title == "New Guidance on Menstrual Products in Correctional Facilities"
    finally:
        adapter.close()


# --- PlaywrightAdapter (deliberate stub) ---------------------------------------


def test_playwright_adapter_is_a_documented_stub():
    class _DummyPlaywrightAdapter(PlaywrightAdapter):
        base_url = "https://example.gov"
        source_id = "dummy_playwright"
        jurisdiction = "federal"

    adapter = _DummyPlaywrightAdapter()
    with pytest.raises(NotImplementedError, match="deferred stub"):
        adapter.fetch()
    with pytest.raises(NotImplementedError, match="deferred stub"):
        adapter.normalize(None)  # type: ignore[arg-type]
