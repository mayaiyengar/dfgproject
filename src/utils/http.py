"""Shared HTTP client, retry/backoff, rate limiting, and robots.txt checking.

Every adapter that talks to the network goes through this module rather than
calling httpx directly — this is where the project's compliance principles
(descriptive User-Agent, reasonable request delays, respect robots.txt) are
enforced once, not re-implemented per adapter.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from utils.config import get_settings

logger = logging.getLogger(__name__)


def build_http_client(*, base_url: str = "", timeout: float | None = None) -> httpx.Client:
    """A pre-configured client: descriptive User-Agent, sane timeout. Adapters
    should get their client from here rather than instantiating httpx.Client
    directly, so the User-Agent is never accidentally left as httpx's default
    (which government sites can reasonably treat as an unidentified bot)."""
    settings = get_settings()
    return httpx.Client(
        base_url=base_url,
        timeout=timeout if timeout is not None else settings.http_timeout_seconds,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int | None = None,
    **kwargs,
) -> httpx.Response:
    """GET/POST/etc. with exponential backoff on transient failures (timeouts,
    429, 5xx) — architecture.md §9. Non-retryable errors (4xx other than 429)
    raise immediately on the first attempt; a hard retry cap prevents one
    flaky source from stalling an entire run.
    """
    settings = get_settings()
    retries = settings.http_max_retries if max_retries is None else max_retries
    retryer = Retrying(
        stop=stop_after_attempt(retries + 1),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    for attempt in retryer:
        with attempt:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
    raise RuntimeError("unreachable")  # Retrying always either returns or raises


class RateLimiter:
    """Sliding-window rate limiter: blocks (sleeps) so no more than
    `max_calls` happen within any `period_seconds` window.

    Deliberately simple and in-process — this project runs as a single
    scheduled batch job (architecture.md §8), not a distributed system, so a
    per-process limiter is sufficient. `sleep_fn`/`time_fn` are injectable
    purely so tests can run this logic without a real clock.
    """

    def __init__(
        self,
        max_calls: int,
        period_seconds: float,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_calls <= 0 or period_seconds <= 0:
            raise ValueError("max_calls and period_seconds must be positive")
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._sleep = sleep_fn
        self._time = time_fn
        self._calls: deque[float] = deque()

    def _evict_expired(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.period_seconds:
            self._calls.popleft()

    def acquire(self) -> None:
        now = self._time()
        self._evict_expired(now)
        if len(self._calls) >= self.max_calls:
            wait_for = self.period_seconds - (now - self._calls[0])
            if wait_for > 0:
                self._sleep(wait_for)
            now = self._time()
            self._evict_expired(now)
        self._calls.append(now)


def robots_allows(robots_txt_content: str, url: str, user_agent: str) -> bool:
    """Pure parsing logic, testable without network: does this already-fetched
    robots.txt content permit `user_agent` to fetch `url`?"""
    parser = RobotFileParser()
    parser.parse(robots_txt_content.splitlines())
    return parser.can_fetch(user_agent, url)


def fetch_robots_allows(client: httpx.Client, url: str, user_agent: str) -> bool:
    """Fetch and check robots.txt for `url`'s host. If robots.txt can't be
    fetched at all (404, network error), the source has published no
    restriction we can read, so this defaults to allow — consistent with
    standard crawler behavior (RFC 9309) and noted explicitly here rather
    than silently: a 5xx on robots.txt itself is logged as a warning since it
    may indicate the site is having problems, not that it permits everything.
    """
    robots_url = urljoin(url, "/robots.txt")
    try:
        response = client.get(robots_url, timeout=10)
    except httpx.TransportError:
        logger.warning("Could not fetch robots.txt at %s — defaulting to allow", robots_url)
        return True
    if response.status_code >= 500:
        logger.warning(
            "robots.txt at %s returned %s — defaulting to allow but this should be investigated",
            robots_url,
            response.status_code,
        )
        return True
    if response.status_code >= 400:
        return True  # no robots.txt published = no restriction to respect
    return robots_allows(response.text, url, user_agent)
