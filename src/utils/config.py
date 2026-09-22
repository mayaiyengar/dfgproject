"""Environment/config loading. Every value here has a safe default or is
genuinely optional — the pipeline must run with none of these set except
where a specific adapter's docstring says otherwise (e.g. Open States
requires OPENSTATES_API_KEY; the pipeline as a whole never requires
ANTHROPIC_API_KEY — architecture.md design goal #2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

_DEFAULT_USER_AGENT = (
    "DfG-Policy-Monitor/0.1 "
    "(Days for Girls International internal policy-monitoring tool; "
    "contact: advocacy@daysforgirls.org; "
    "https://github.com/mayaiyengar/dfgproject)"
)


@dataclass(frozen=True)
class Settings:
    # Optional — stage-2 AI classification is skipped entirely if unset.
    anthropic_api_key: str | None
    # Required only by adapters that call Open States; unset = that adapter
    # raises a clear config error when actually invoked, not at import time.
    openstates_api_key: str | None
    congress_api_key: str | None
    database_url: str
    user_agent: str
    http_timeout_seconds: float
    http_max_retries: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        openstates_api_key=os.environ.get("OPENSTATES_API_KEY") or None,
        congress_api_key=os.environ.get("CONGRESS_API_KEY") or None,
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./dfg_policy.db"),
        user_agent=os.environ.get("DFG_USER_AGENT", _DEFAULT_USER_AGENT),
        http_timeout_seconds=float(os.environ.get("DFG_HTTP_TIMEOUT_SECONDS", "30")),
        http_max_retries=int(os.environ.get("DFG_HTTP_MAX_RETRIES", "3")),
    )
