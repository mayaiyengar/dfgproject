"""Last-resort base for JS-rendered pages (architecture.md §3.2).

Deliberately a stub, not a working implementation. None of the researched
Federal, California, or Washington sources require JavaScript rendering
(source-inventory.md) — West Virginia and Wyoming's ASPX query-based
registers came closest, and even those are form-submission, not JS-rendered,
concerns. Building real headless-browser plumbing (a Playwright dependency,
browser lifecycle management, wait-condition handling) for a source that
doesn't exist yet in this project's scope would be exactly the kind of
invented behavior the implementation rules for this project prohibit.

Implement this for real only when a specific, confirmed future source
requires it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from ingestion.base.adapter import SourceAdapter
from normalization.schema import PolicyIn, RawRecord

_NOT_IMPLEMENTED_MESSAGE = (
    "PlaywrightAdapter is a deferred stub (see module docstring) — no source "
    "in the current project scope requires JS rendering. Implement this only "
    "when a specific, confirmed source needs it."
)


class PlaywrightAdapter(SourceAdapter):
    base_url: str

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MESSAGE)

    def normalize(self, raw: RawRecord) -> PolicyIn:
        raise NotImplementedError(_NOT_IMPLEMENTED_MESSAGE)
