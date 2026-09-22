"""content_hash computation for change detection (architecture.md §7).

The hash covers the fields whose change actually matters for "has this
policy meaningfully changed" — not every field on PolicyIn. Notably it
excludes raw_payload (which can shuffle key order or include volatile
noise from the source with no substantive change) and description is
included since a substantive text edit to a summary is a real change.
"""

from __future__ import annotations

import hashlib

from normalization.schema import PolicyIn

_HASH_FIELDS = (
    "title",
    "normalized_status",
    "official_status",
    "last_action_date",
    "description",
)


def compute_content_hash(policy: PolicyIn) -> str:
    """Deterministic sha256 over the change-detection-relevant fields.

    Does not trust any source's own "last modified" flag (architecture.md
    §7) — this is computed the same way regardless of what the source claims.
    """
    parts: list[str] = []
    for field_name in _HASH_FIELDS:
        value = getattr(policy, field_name)
        if hasattr(value, "value"):  # Enum
            value = value.value
        parts.append("" if value is None else str(value))
    joined = "\x1f".join(parts)  # unit separator avoids accidental collisions from concatenation
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
