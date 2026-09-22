"""Loads config/sources.yaml — the version-controlled declaration of which
sources exist and their static config (roadmap.md §2 repo-structure
rationale: source_registry rows are declared in a reviewable file, not only
editable by hand in a database).

This is the config-loading half only. The `source_registry` *table*
(enabled/disabled at runtime, `consecutive_failures`, `last_checked_at`) is
built in the database milestone — this module doesn't touch a database.
`SourceConfig` here is the static subset of that table's columns.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from models.enums import Level, PolicyType, SourceType


class SourceConfig(BaseModel):
    source: str
    jurisdiction: str
    state: str | None = None
    level: Level
    policy_types_covered: list[PolicyType]
    source_type: SourceType
    base_url: str
    api_url: str | None = None
    auth_required: bool = False
    enabled: bool = True
    check_frequency: str = "daily"
    notes: str | None = None


def load_source_registry(path: str | Path = "config/sources.yaml") -> list[SourceConfig]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Source registry config not found at {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    entries = raw.get("sources", [])
    return [SourceConfig.model_validate(entry) for entry in entries]
