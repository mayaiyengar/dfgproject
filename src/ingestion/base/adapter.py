"""The interface every source adapter implements (architecture.md §3.1).

Implemented as an ABC rather than a `typing.Protocol` — a deliberate,
disclosed deviation from the architecture doc's sketch. A Protocol can't hold
shared constructor logic (HTTP client setup, rate limiters), which the
concrete base classes in this package (RestApiAdapter, HtmlListAdapter, etc.)
need. The contract itself — `fetch()` then `normalize()`, kept separate so
tests can drive `normalize()` from fixtures with no network — is unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable

from normalization.schema import PolicyIn, RawRecord


class SourceAdapter(ABC):
    source_id: str
    """Matches source_registry.source — set by each concrete adapter."""

    jurisdiction: str
    """'federal', a state postal code, or 'dc' — set by each concrete adapter."""

    @abstractmethod
    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        """Pull candidate records. `since` is a hint, not a guarantee —
        adapters that can't filter server-side return everything and let
        downstream dedup/change-detection do the work (architecture.md §1)."""
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw: RawRecord) -> PolicyIn:
        """Map one source-specific raw record into the canonical schema."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any held resources (HTTP client, etc.). Base classes that
        open a client override this; adapters with no such resource can
        leave the no-op default."""
        return None

    def __enter__(self) -> "SourceAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
