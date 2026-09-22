"""Canonical enums shared across ingestion, normalization, classification, and
the database layer. Values here must stay in sync with docs/database-schema.md
and docs/architecture.md §4 — those documents are the source of truth for
what values are allowed; this module is just where they're enforced in code.
"""

from __future__ import annotations

from enum import Enum


class Level(str, Enum):
    FEDERAL = "federal"
    STATE = "state"
    DC = "dc"


class PolicyType(str, Enum):
    BILL = "bill"
    REGULATION = "regulation"
    EXECUTIVE_ORDER = "executive_order"
    GUIDANCE = "guidance"
    PROGRAM = "program"
    APPROPRIATION = "appropriation"
    BALLOT_MEASURE = "ballot_measure"
    ADMINISTRATIVE_ACTION = "administrative_action"
    OTHER = "other"


class NormalizedStatus(str, Enum):
    """Canonical status values. `official_status` on PolicyIn always retains
    the source's verbatim string alongside this normalized value — see
    architecture.md §4."""

    INTRODUCED = "introduced"
    IN_COMMITTEE = "in_committee"
    PASSED_CHAMBER = "passed_chamber"
    PASSED_LEGISLATURE = "passed_legislature"
    ENACTED = "enacted"
    VETOED = "vetoed"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"
    PROPOSED = "proposed"  # proposed rule / notice of proposed rulemaking
    ADOPTED = "adopted"  # for regulations
    EFFECTIVE = "effective"
    EXPIRED = "expired"
    UNKNOWN = "unknown"  # source gave a status string we can't map yet — never silently dropped


class SourceType(str, Enum):
    API = "api"
    RSS = "rss"
    HTML_SCRAPE = "html_scrape"
    PDF = "pdf"
    JS_RENDERED = "js_rendered"


class ClassificationMethod(str, Enum):
    RULE_BASED = "rule_based"
    AI_ASSISTED = "ai_assisted"
    RULE_BASED_ONLY = "rule_based_only"  # ambiguous stage-1 result, no API key configured


class ChangeType(str, Enum):
    NEW = "new"
    STATUS_CHANGE = "status_change"
    DATE_CHANGE = "date_change"
    TEXT_CHANGE = "text_change"
    OTHER = "other"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED_RELEVANT = "confirmed_relevant"
    CONFIRMED_NOT_RELEVANT = "confirmed_not_relevant"
    NEEDS_MORE_INFO = "needs_more_info"


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class SourceRunStatus(str, Enum):
    """Per-source result within a single scraper_run — see scraper_run_sources
    in database-schema.md. Deliberately distinct from RunStatus (the whole-run
    status) so a source can be 'failed' inside an overall 'partial_failure' run."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED_DISABLED = "skipped_disabled"


class Category(str, Enum):
    """Topic taxonomy from the project brief. Populated by the stage-1 rule
    engine (Milestone: classification) and/or stage-2 AI classification.
    Not built or consumed by the adapter framework itself."""

    PRODUCT_ACCESS = "product_access"
    SCHOOLS = "schools"
    TAXATION = "taxation"
    PUBLIC_HEALTH = "public_health"
    HEALTHCARE = "healthcare"
    WORKPLACE = "workplace"
    CORRECTIONS = "corrections"
    YOUTH_AND_EDUCATION = "youth_and_education"
    SOCIAL_SERVICES = "social_services"
    LOW_INCOME = "low_income"
    GENDER_EQUITY = "gender_equity"
    DISABILITY_ACCESSIBILITY = "disability_accessibility"
    ENVIRONMENTAL_SUSTAINABILITY = "environmental_sustainability"
    GOVERNMENT_FUNDING_RESEARCH = "government_funding_research"
    OTHER = "other"


class AffectedPopulation(str, Enum):
    STUDENTS = "students"
    INCARCERATED = "incarcerated"
    LOW_INCOME = "low_income"
    DISABLED = "disabled"
    YOUTH = "youth"
    UNINSURED = "uninsured"
    GENERAL_PUBLIC = "general_public"
    OTHER = "other"
