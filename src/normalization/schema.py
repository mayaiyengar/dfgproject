"""The canonical schema every adapter normalizes into.

Every `SourceAdapter.normalize()` (see src/ingestion/base/adapter.py) must
return a `PolicyIn`. Nothing downstream of normalization — dedup, change
detection, classification, persistence — deals with source-specific shapes
again. This is the contract that lets 50+ heterogeneous sources share one
pipeline (architecture.md §4).

Fields here are a subset of the `policies` table in database-schema.md:
adapter-facing fields only. Surrogate `id`, `content_hash`, `first_seen_at` /
`last_seen_at` / `last_updated_at`, and `is_new` / `is_updated` are computed
by the pipeline (Milestone: database + change detection), not by adapters.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from models.enums import Level, NormalizedStatus, PolicyType


class RawRecord(BaseModel):
    """The envelope every adapter's `fetch()` yields, before normalization.

    `data` is intentionally untyped (`dict[str, Any]`) because raw shapes are
    wildly different across sources — a JSON API response, a dict of fields
    scraped off an HTML page, or extracted PDF text plus metadata. Adapters
    are expected to put whatever they parsed into `data`; `normalize()` is
    where that gets mapped into a `PolicyIn`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: dict[str, Any]
    source_url: str
    fetched_at: datetime
    raw_document_bytes: bytes | None = None
    """Set when the adapter downloaded an underlying document (PDF, etc.)
    separately from the listing/API payload. Not persisted by this
    milestone's pipeline — reserved for the policy_snapshots retention work
    (architecture.md §13), built in the database milestone."""


class PolicyIn(BaseModel):
    """Canonical normalized policy record. Validated before anything touches
    the database (architecture.md §4)."""

    model_config = ConfigDict(use_enum_values=False)

    source: str
    """Matches source_registry.source, e.g. 'federal_register_eo', 'ca_regulations'."""

    external_id: str
    """The source's own stable identifier — bill number+session, docket #, EO #.
    This, not the DB's surrogate id, is the dedup key (architecture.md §5)."""

    title: str
    short_title: str | None = None
    bill_number: str | None = None

    policy_type: PolicyType
    jurisdiction: str
    state: str | None = None
    level: Level
    chamber: str | None = None
    session: str | None = None

    description: str | None = None

    official_status: str | None = None
    normalized_status: NormalizedStatus = NormalizedStatus.UNKNOWN

    introduction_date: date | None = None
    last_action_date: date | None = None
    effective_date: date | None = None
    expiration_date: date | None = None

    source_url: str
    official_text_url: str | None = None

    raw_payload: dict[str, Any] = {}
    """The adapter's raw source response, kept for debugging/re-normalization
    (architecture.md §13) — must be JSON-serializable."""

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title must not be blank — provenance requires a real title, not a placeholder")
        return v.strip()

    @field_validator("source_url")
    @classmethod
    def source_url_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "source_url must not be blank — provenance is a hard requirement "
                "(architecture.md §1 design goals), never optional"
            )
        return v.strip()

    @field_validator("raw_payload")
    @classmethod
    def raw_payload_json_serializable(cls, v: dict[str, Any]) -> dict[str, Any]:
        # Deliberately no `default=str` fallback — that would silently
        # stringify anything with a __str__ (e.g. a BeautifulSoup Tag) instead
        # of catching the adapter bug of passing a non-primitive raw value.
        try:
            json.dumps(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"raw_payload must be JSON-serializable: {exc}") from exc
        return v
