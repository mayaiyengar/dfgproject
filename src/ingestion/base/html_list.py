"""Base class for HTML listing-page sources (architecture.md §3.2) — state
regulation registers and executive-order index pages, the majority pattern
found across the state layer (source-inventory.md §5).

Provides fetch-and-parse plumbing (BeautifulSoup); selector logic and
pagination-following are source-specific and live in each concrete adapter.
"""

from __future__ import annotations

from typing import Any

import httpx
from bs4 import BeautifulSoup

from ingestion.base.adapter import SourceAdapter
from utils.http import build_http_client, request_with_retry


class HtmlListAdapter(SourceAdapter):
    base_url: str

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client or build_http_client(base_url=self.base_url)
        self._owns_client = client is None

    def _get_soup(self, path: str, params: dict[str, Any] | None = None) -> BeautifulSoup:
        response = request_with_retry(self._client, "GET", path, params=params)
        return BeautifulSoup(response.text, "lxml")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
