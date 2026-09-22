# Database Schema

Status: proposed, pending approval. This refines the schema sketched in the project brief — same entities, with explicit types, keys, indexes, and a couple of structural changes explained inline. Nothing here is implemented yet; this is the target for the Alembic migrations in Phase 2.

Engine: PostgreSQL in shared/production use, SQLite for local dev (SQLAlchemy models are portable across both; avoid Postgres-only types in the core schema).

## Entity overview

```
source_registry ──┐
                   │ 1:N
                   ▼
              policies ──1:N── classifications
                   │
                   ├──1:N── policy_changes
                   │
                   └──1:N── reviews

scraper_runs ──1:N── scraper_run_sources ──N:1── source_registry
```

## `source_registry`

One row per monitored source (not per jurisdiction — a state can have multiple sources: a bill-tracking adapter, a regulation-register adapter, an EO adapter).

| Column | Type | Notes |
|---|---|---|
| `id` | PK (int/uuid) | |
| `source` | text, unique | short slug, e.g. `openstates_bills`, `ca_regulations`, `federal_register_eo` |
| `jurisdiction` | text | `federal`, `dc`, or a state name |
| `state` | char(2), nullable | postal code, null for federal |
| `level` | enum | `federal` \| `state` \| `dc` |
| `policy_types_covered` | text[] / JSON | e.g. `['bill']`, `['regulation']`, `['executive_order']` — a source is scoped to what it actually produces |
| `source_type` | enum | `api` \| `rss` \| `html_scrape` \| `pdf` \| `js_rendered` |
| `base_url` | text | |
| `api_url` | text, nullable | |
| `auth_required` | boolean | |
| `enabled` | boolean, default true | operator can disable a broken/deprecated source without deleting history |
| `check_frequency` | text | e.g. `daily`, `weekly` — read by the scheduler |
| `last_checked_at` | timestamptz, nullable | |
| `consecutive_failures` | int, default 0 | drives the `needs_attention` flag described in `architecture.md` §9 |
| `notes` | text, nullable | free text: quirks, known limitations, links to docs |

Indexes: unique on `source`; index on `(jurisdiction, enabled)` for the dashboard's source-health filters.

## `policies`

The core table — one row per distinct policy item, kept current (mutable) with history captured separately in `policy_changes`.

| Column | Type | Notes |
|---|---|---|
| `id` | PK (uuid) | surrogate key; never reused as a cross-source identity |
| `source_id` | FK → `source_registry.id` | which adapter produced/owns this record |
| `external_id` | text | the source's own stable identifier (bill number+session, docket #, EO #) |
| `title` | text | |
| `short_title` | text, nullable | |
| `bill_number` | text, nullable | populated for legislation only |
| `policy_type` | enum | `bill` \| `regulation` \| `executive_order` \| `guidance` \| `program` \| `appropriation` \| `ballot_measure` \| `administrative_action` \| `other` |
| `jurisdiction` | text | denormalized for query convenience (avoids a join for every dashboard filter) |
| `state` | char(2), nullable | |
| `level` | enum | `federal` \| `state` \| `dc` |
| `chamber` | text, nullable | `house`/`senate`/`unicameral`/null for non-legislative types |
| `session` | text, nullable | legislative session identifier, source-specific format preserved verbatim |
| `description` | text, nullable | summary/abstract as provided by the source |
| `official_status` | text, nullable | verbatim source status string |
| `normalized_status` | enum | see `architecture.md` §4 for the canonical value list |
| `introduction_date` | date, nullable | |
| `last_action_date` | date, nullable | |
| `effective_date` | date, nullable | |
| `expiration_date` | date, nullable | |
| `source_url` | text | the page this record was fetched/derived from |
| `official_text_url` | text, nullable | direct link to bill/rule/EO full text, when available |
| `first_seen_at` | timestamptz | set once, on first insert |
| `last_seen_at` | timestamptz | bumped every run the record is still present at the source |
| `last_updated_at` | timestamptz, nullable | bumped only when `content_hash` changes |
| `content_hash` | text | sha256 over the fields used for change detection (see `architecture.md` §7) |
| `is_new` | boolean | true only for the run that first inserted it; dashboard "New" view is really `first_seen_at` within window, this flag is a convenience |
| `is_updated` | boolean | true if the most recent run changed `content_hash` |
| `raw_payload` | JSON, nullable | the unmodified source response, kept for debugging/re-normalization if mapping logic changes later |
| `created_at` / `updated_at` | timestamptz | standard bookkeeping |

Indexes: unique on `(source_id, external_id)` (the exact-dedup key from `architecture.md` §5); index on `(jurisdiction, policy_type, normalized_status)` for dashboard filters; index on `last_action_date` and on `first_seen_at` (both drive the "recent" views); a secondary non-unique index on `(jurisdiction, bill_number, session)` to support the cross-source dedup heuristic.

## `classifications`

Append-only log — a policy can be classified more than once (rule engine v1, rule engine v2, an AI pass, a re-run after a prompt change). The dashboard shows the latest row per policy; history is kept for auditing classifier drift.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `policy_id` | FK → `policies.id` | |
| `is_relevant` | boolean, nullable | null = undetermined, pending human triage |
| `confidence` | float, nullable | 0–1, null for pure rule-based hits (which are boolean, not scored) |
| `classification_method` | enum | `rule_based` \| `ai_assisted` \| `rule_based_only` (ambiguous, no API key configured) |
| `categories` | text[] / JSON | topic taxonomy tags from the project brief (product access, schools, taxation, etc.) |
| `affected_populations` | text[] / JSON | e.g. `students`, `incarcerated`, `low_income`, `disabled` |
| `policy_mechanism` | text, nullable | e.g. `funding`, `tax_exemption`, `mandate`, `study/report` |
| `reason` | text, nullable | grounded explanation; for AI classifications, must reference supplied text (see `architecture.md` §6) |
| `model` | text, nullable | e.g. `claude-sonnet-5`; null for rule-based |
| `prompt_version` | text, nullable | |
| `matched_keywords` | text[] / JSON, nullable | which rule-engine terms fired, for rule-based transparency |
| `created_at` | timestamptz | |

Index: `(policy_id, created_at desc)` to fetch the latest classification quickly.

## `policy_changes`

Append-only audit trail of field-level changes, written whenever change detection (architecture.md §7) finds a diff.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `policy_id` | FK → `policies.id` | |
| `change_type` | enum | `new` \| `status_change` \| `date_change` \| `text_change` \| `other` |
| `field_changed` | text, nullable | column name, null for `change_type='new'` |
| `old_value` | text, nullable | |
| `new_value` | text, nullable | |
| `detected_at` | timestamptz | |
| `source_url` | text | url at time of detection (sources occasionally restructure URLs) |

Index: `(policy_id, detected_at desc)`.

## `reviews`

Human-in-the-loop QA, deliberately separate from `classifications` (machine output) so staff review status is never confused with — or overwritten by — a re-classification run.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `policy_id` | FK → `policies.id` | |
| `review_status` | enum | `pending` \| `confirmed_relevant` \| `confirmed_not_relevant` \| `needs_more_info` |
| `reviewer` | text | staff identifier/email |
| `reviewed_at` | timestamptz | |
| `notes` | text, nullable | |

One policy can accumulate multiple review rows over time (re-reviewed after an update); dashboard shows the latest by `reviewed_at`. This table is also the ground truth used to compute classifier agreement (`roadmap.md` metrics): compare `reviews.review_status` to the matching `classifications.is_relevant` at the time of review.

## `scraper_runs`

One row per scheduled/manual pipeline execution.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `started_at` / `finished_at` | timestamptz | |
| `status` | enum | `running` \| `success` \| `partial_failure` \| `failed` |
| `sources_attempted` | int | |
| `sources_succeeded` | int | |
| `sources_failed` | int | |
| `records_found` | int | total normalized records seen across all sources |
| `new_records` | int | |
| `updated_records` | int | |
| `relevant_records` | int | records with `is_relevant=true` after this run's classification pass |
| `errors` | JSON, nullable | list of `{source, error}` for quick triage without opening logs |

## `scraper_run_sources`

**Addition to the brief's schema.** The brief's `scraper_runs` table stores only aggregate counts (`sources succeeded`/`failed`), which is exactly the "silently looks like 0 policies found" failure mode the project explicitly wants to avoid — an aggregate count can't tell you *which* source failed. This child table gives per-source visibility for every run.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `run_id` | FK → `scraper_runs.id` | |
| `source_id` | FK → `source_registry.id` | |
| `status` | enum | `success` \| `failed` \| `skipped_disabled` |
| `records_found` | int | |
| `new_records` | int | |
| `updated_records` | int | |
| `error_message` | text, nullable | |
| `duration_seconds` | float, nullable | feeds the "average scraper runtime" metric |

Index: `(source_id, run_id desc)` — this is what the dashboard's Source Health page queries to show each source's recent run history and flag `needs_attention`.

## Notes on the brief's original schema

- Renamed the brief's `source_registry` fields into the split above (`source_registry` = config, `scraper_run_sources` = per-run results) rather than one table trying to be both static config and dynamic run history.
- `classifications` and `policy_changes` and `reviews` are modeled as append-only logs rather than 1:1 with `policies`, since the brief's own requirements (track classifier history, track change history, allow re-review) imply history, not a single mutable row.
- `raw_payload` on `policies` wasn't in the brief; added because re-normalization after a mapping bug fix is much cheaper with the original payload retained, and storage cost is negligible at this volume.
