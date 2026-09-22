"""Adapter-level failure categories.

Added during the Federal source-adapter milestone (not part of the original
Milestone 1 framework) because the implementation rules for this project
require adapters to distinguish, at minimum: a successful zero-result
response, a successful response with records, rate limiting, auth/config
failure, server/network failure, and a malformed/unexpected response. HTTP
retry/backoff (utils.http) already handles *transient* retries; this module
is where an adapter, once retries are exhausted or a non-retryable error
occurs, classifies *what kind* of failure it is — so a caller (the run
logger, once built) can record a distinguishable status instead of a bare
traceback (architecture.md §9).
"""

from __future__ import annotations

import httpx


class SourceError(Exception):
    """Base for all adapter-raised errors. Never let a raw, unclassified
    exception from an adapter propagate — catch and re-raise as one of the
    subclasses below so failures are distinguishable, not just 'it broke'."""


class SourceAuthError(SourceError):
    """Missing/invalid credentials, or other adapter configuration problem
    (e.g. a required API key env var is unset). Distinguishable from a
    network/server failure: this needs a human to fix config, not a retry."""


class SourceRateLimitedError(SourceError):
    """The source's rate limit was hit and retries (utils.http) were
    exhausted without success."""


class SourceUnavailableError(SourceError):
    """A transient server or network failure, and retries were exhausted."""


class SourceResponseError(SourceError):
    """The response was received (2xx, no transport error) but doesn't match
    the shape the adapter expects — missing keys, wrong types, or a document
    type/status combination the adapter doesn't know how to normalize. Kept
    distinct from SourceUnavailableError because this usually means the
    source's API changed, not that it's down."""


def classify_http_error(exc: httpx.HTTPStatusError) -> SourceError:
    """Maps an exhausted-retries HTTPStatusError to the right category.
    Adapters call this in their except-block rather than letting the raw
    httpx exception propagate."""
    status = exc.response.status_code
    if status in (401, 403):
        return SourceAuthError(f"HTTP {status} from {exc.request.url} — check credentials/configuration")
    if status == 429:
        return SourceRateLimitedError(f"HTTP 429 from {exc.request.url} — rate limit exceeded, retries exhausted")
    if status >= 500:
        return SourceUnavailableError(f"HTTP {status} from {exc.request.url} — server error, retries exhausted")
    return SourceResponseError(f"HTTP {status} from {exc.request.url} — unexpected client error")


def classify_transport_error(exc: httpx.TransportError) -> SourceError:
    return SourceUnavailableError(f"Network/transport error, retries exhausted: {exc}")
