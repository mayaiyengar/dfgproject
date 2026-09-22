"""Base class for RSS/Atom feed sources (architecture.md §3.2). Not exercised
by any Phase 1 (Federal + CA + WA) adapter — included now because it's part
of the documented adapter framework, not because a specific source needs it
yet. Built generically and covered by its own fixture-based test so it's
ready when a future source (e.g. an agency's Federal Register RSS filter) is
worth using over its JSON API.
"""

from __future__ import annotations

import feedparser
import httpx

from ingestion.base.adapter import SourceAdapter
from utils.http import build_http_client, request_with_retry


class RssFeedAdapter(SourceAdapter):
    feed_url: str

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client or build_http_client()
        self._owns_client = client is None

    def _get_feed(self) -> feedparser.FeedParserDict:
        response = request_with_retry(self._client, "GET", self.feed_url)
        return feedparser.parse(response.content)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
