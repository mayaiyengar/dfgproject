"""Base class for JSON REST API sources (architecture.md §3.2).

Handles: HTTP client lifecycle, retry/backoff (via utils.http), and rate
limiting. Deliberately does NOT impose a generic pagination scheme — Federal
Register (next-page URL), Congress.gov (offset/limit), and Open States
(page numbers) all paginate differently, and forcing one abstraction onto
all three would be exactly the kind of premature generalization the
architecture doc warns against. Concrete adapters implement their own
`fetch()` loop using `_get_json()`.
"""

from __future__ import annotations

from typing import Any

import httpx

from ingestion.base.adapter import SourceAdapter
from utils.http import RateLimiter, build_http_client, request_with_retry


class RestApiAdapter(SourceAdapter):
    base_url: str

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        rate_limiters: list[RateLimiter] | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._client = client or build_http_client(base_url=self.base_url)
        self._rate_limiters = rate_limiters or []
        self._owns_client = client is None
        self._max_retries = max_retries
        """Overrides utils.config.Settings.http_max_retries for this adapter
        instance. Mainly so error-path tests can set this to 0 and fail fast
        instead of sleeping through real exponential backoff."""

    def _auth_headers(self) -> dict[str, str]:
        """Override in a concrete adapter that needs an API key header."""
        return {}

    def _auth_params(self) -> dict[str, Any]:
        """Override in a concrete adapter that passes its API key as a query
        param (e.g. an api.data.gov key) rather than a header."""
        return {}

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for limiter in self._rate_limiters:
            limiter.acquire()
        merged_params = {**self._auth_params(), **(params or {})}
        response = request_with_retry(
            self._client,
            "GET",
            path,
            params=merged_params or None,
            headers=self._auth_headers() or None,
            max_retries=self._max_retries,
        )
        return response.json()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
