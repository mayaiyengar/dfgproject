# Architecture

Status: proposed, pending approval. Nothing in this document has been implemented yet.

## 1. Design goals

- **Reusable adapters, not 50 scripts.** A small set of base adapter classes (REST API, RSS/Atom, HTML scrape, PDF, JS-rendered) that source-specific adapters extend by supplying config + parsing logic, not by reimplementing HTTP/retry/rate-limit handling.
- **Works with zero AI configured.** Rule-based relevance filtering is the load-bearing layer. The Claude API is an optional second-stage refinement, never a dependency for the pipeline to run.
- **No silent failure.** A source that returns 0 records because it's broken must look different in the data from a source that returns 0 records because nothing new was filed.
- **Provenance first.** Every stored policy traces to a `source_url` and (where available) an `official_text_url`, plus the raw payload hash used to detect changes.
- **Incremental, not archival.** The system is optimized to catch *new* and *changed* items on a recurring cadence, not to backfill history. Adapters should support a "since last successful run" query where the source allows it, and fall back to "fetch current list, diff against what we've already stored" where it doesn't.

## 2. High-level data flow

```
 ┌───────────────────────────────────────────────────────────────────┐
 │                         source_registry (config)                    │
 │   which sources are enabled, their base/api URLs, jurisdiction      │
 └───────────────────────────────┬─────────────────────────────────────┘
                                  │ drives
                                  ▼
 ┌───────────────┐   ┌───────────────────┐   ┌──────────────────────┐
 │  1. FETCH      │──▶│  2. NORMALIZE      │──▶│  3. HASH + DEDUPE     │
 │  adapter.fetch │   │  raw → PolicyIn    │   │  content_hash,        │
 │  (per source)  │   │  (pydantic model)  │   │  match on external_id │
 └───────────────┘   └───────────────────┘   └──────────┬────────────┘
                                                          ▼
                              ┌───────────────────────────────────────┐
                              │  4. RELEVANCE — STAGE 1 (rules)         │
                              │  keyword/regex/taxonomy match           │
                              │  runs on every record, no API key needed│
                              └──────────────┬───────────────────────┘
                                             ▼
                       ┌───────────────────────────────────────────┐
                       │ 5. RELEVANCE — STAGE 2 (optional, AI)        │
                       │ only for ambiguous stage-1 candidates;       │
                       │ skipped entirely if no ANTHROPIC_API_KEY     │
                       └──────────────┬────────────────────────────┘
                                      ▼
                  ┌────────────────────────────────────────────────┐
                  │  6. CHANGE DETECTION                              │
                  │  compare content_hash + key fields vs. stored row │
                  │  → is_new / is_updated / unchanged, policy_changes│
                  └──────────────┬─────────────────────────────────┘
                                 ▼
                  ┌────────────────────────────────────────────────┐
                  │  7. PERSIST (SQLAlchemy upsert)                   │
                  │  policies, classifications, policy_changes        │
                  └──────────────┬─────────────────────────────────┘
                                 ▼
                  ┌────────────────────────────────────────────────┐
                  │  8. RUN BOOKKEEPING                               │
                  │  scraper_runs + per-source result rows            │
                  └──────────────┬─────────────────────────────────┘
                                 ▼
                  ┌────────────────────────────────────────────────┐
                  │  9. DASHBOARD (Streamlit, read + review only)     │
                  │  browse/search/filter/export, mark reviewed       │
                  └────────────────────────────────────────────────┘
```

Steps 1–8 run as a scheduled batch job (GitHub Actions). Step 9 is a separate always-available process that only reads the database and writes to `reviews` — it never triggers scraping itself, so a slow dashboard session can't interfere with ingestion.

## 3. Source adapter pattern

### 3.1 Interface

Every adapter implements one small interface, roughly:

```python
class SourceAdapter(Protocol):
    source_id: str          # matches source_registry.source
    jurisdiction: str       # "federal" | state postal code | "dc"

    def fetch(self, since: datetime | None) -> Iterable[RawRecord]:
        """Pull candidate records. `since` is a hint, not a guarantee —
        adapters that can't filter server-side just return everything
        and let dedup/hashing downstream do the work."""

    def normalize(self, raw: RawRecord) -> PolicyIn:
        """Map source-specific fields into the canonical pydantic schema."""
```

`fetch` and `normalize` are deliberately separate so tests can feed fixture `RawRecord`s into `normalize` without any network access.

### 3.2 Base classes (the reusable part)

Rather than 50 bespoke scrapers, adapters are built by composing a handful of bases that live in `src/ingestion/base/`:

| Base class | Handles | Used for |
|---|---|---|
| `RestApiAdapter` | auth headers, pagination, retry/backoff, rate-limit sleep | Congress.gov, Federal Register, Regulations.gov, Open States/LegiScan |
| `RssFeedAdapter` | feed parsing (feedparser), incremental via `pubDate` | states that publish bill/EO RSS feeds |
| `HtmlListAdapter` | fetch a listing page, follow pagination, parse via a per-source CSS/XPath selector map, fetch detail pages | state regulation registers / EO pages with static HTML |
| `PdfDocumentAdapter` | download PDF, extract text (pdfplumber), regex/heuristic field extraction | states that publish EOs/rules only as PDFs |
| `PlaywrightAdapter` | headless-browser fetch for JS-rendered pages, reused Chromium context, explicit wait conditions | the handful of state sites that are SPA/JS-only (used as a last resort — slower, more fragile, and the first thing checked when a source starts failing) |

A concrete adapter (e.g. `adapters/state/california_regulations.py`) subclasses one base and supplies: base URL(s), a field-mapping function, and any source-specific quirks. Most state regulation/EO adapters are expected to be 40–120 lines of config + mapping, not new scraping engines.

### 3.3 Two tiers of sources

1. **Aggregator-backed** — Open States (or LegiScan) for state/DC *bills*, Congress.gov + GovInfo for federal *bills*, Federal Register for federal *rules and executive orders*. One adapter class, parameterized per jurisdiction, covers most bill-tracking needs across all 50 states without per-state scraping. (Coverage/terms to be confirmed by the research task before this becomes a firm dependency — see `source-inventory.md`.)
2. **Bespoke per-jurisdiction** — state regulation registers, governors' executive order pages, and anything an aggregator doesn't cover. These are the ones that actually vary state-by-state and are why an adapter *pattern* rather than a single monolithic scraper matters.

## 4. Normalization

All adapters emit a `PolicyIn` Pydantic model (subset of the `policies` table — see `database-schema.md`) which is validated before anything touches the database. Normalization responsibilities:

- Map source-specific status strings to a small `normalized_status` enum (e.g. `introduced`, `in_committee`, `passed_chamber`, `passed_legislature`, `enacted`, `vetoed`, `failed`, `withdrawn`, `adopted` [for rules], `effective`, `expired`) plus keep the verbatim `official_status` string for traceability.
- Parse dates into `date`/`datetime` (never leave as source-formatted strings).
- Resolve `policy_type` (bill, regulation, executive_order, guidance, program, appropriation, ballot_measure, administrative_action, other).
- Preserve `external_id` as whatever stable identifier the source uses (bill number + session for bills; docket/rule number for regs; EO number for executive orders) — this is the primary dedup key, not the database's own surrogate `id`.

## 5. Deduplication

Two layers:

1. **Exact match**: `(source_id, external_id)` unique constraint. Re-fetching the same bill just updates the existing row.
2. **Cross-source match** (needed because the same state bill may appear via an Open States adapter *and* a state open-data adapter, or a federal bill may be discussed in both Congress.gov and a Federal Register notice): a secondary match on `(jurisdiction, bill_number, session)` normalized (strip whitespace/case, standardize prefixes like "HB"/"H.B."/"House Bill"). When two sources disagree on non-identity fields (e.g., status), the record keeps both: the "primary" source's normalized fields win, and the secondary source's raw contribution is logged, not silently dropped. This is a heuristic, not a promise of perfect entity resolution — see `risks-and-limitations.md`.

## 6. Relevance classification (two-stage)

**Stage 1 — deterministic, always runs, no API key required.**
A rule engine (`src/classification/rules.py`) matches normalized title + summary/description (+ full text when cheaply available) against:
- a curated keyword/phrase list (e.g., "menstrual", "period product", "tampon", "sanitary napkin", "feminine hygiene") — high-confidence, short-circuits to `is_candidate=True`
- a topic co-occurrence list (e.g., "school" + "hygiene products", "incarcerated" + "hygiene", "sales tax exemption" + "feminine hygiene") for policies that don't name menstrual products directly in the title
- an explicit negative list to suppress common false positives (e.g., generic "women's health" bills with no menstrual-specific language)

Every record gets a stage-1 result: `not_candidate`, `candidate_high_confidence`, or `candidate_ambiguous`. `not_candidate` records are still stored (for completeness/audit) but flagged `is_relevant=false, classification_method='rule_based'` and excluded from the default dashboard view.

**Stage 2 — optional, AI-assisted, only for `candidate_ambiguous`.**
If `ANTHROPIC_API_KEY` is configured, ambiguous records are sent to Claude with a structured-output prompt (versioned in `src/classification/prompts/`) requiring:
- `is_relevant: bool`, `confidence: float`
- `categories: list[enum]`, `affected_populations: list[enum]`, `policy_mechanism: str`
- `reason: str` that must quote/reference the actual policy text supplied (no external knowledge, no invented facts — the prompt explicitly instructs the model to say "insufficient information" rather than guess)
- validated against a Pydantic schema; a response that fails validation is retried once, then falls back to `needs_human_review` rather than being discarded

Every classification row stores `model`, `prompt_version`, `classification_method` (`rule_based` | `ai_assisted`), and timestamp — so later, a human reviewer's `reviews` verdict can be compared against the classifier's `is_relevant` to compute agreement rate (see `roadmap.md` metrics). If no API key is present, `candidate_ambiguous` records are simply queued as `classification_method='rule_based_only', is_relevant=null` for a human to triage in the dashboard — the pipeline does not block or degrade.

## 7. Change detection

Change detection does **not** trust a source's own "new" flag. For every normalized record:

1. Compute `content_hash = sha256(title + normalized_status + official_status + last_action_date + description)`.
2. Look up existing row by `(source_id, external_id)`.
   - No existing row → insert, `is_new=true`, `first_seen_at=last_seen_at=now()`.
   - Existing row, same `content_hash` → `unchanged`: only bump `last_seen_at`.
   - Existing row, different `content_hash` → `is_updated=true`, `last_updated_at=now()`, diff the individual normalized fields and write one `policy_changes` row per changed field (`field_changed`, `old_value`, `new_value`, `change_type` in `status_change | date_change | text_change | other`).
3. A policy not seen in the latest fetch of a source that previously listed it is *not* deleted — it's left alone (sources rotate what's "current" constantly; absence isn't a reliable signal) but this is visible via `last_seen_at` staleness in the dashboard.

## 8. Scheduling

- GitHub Actions scheduled workflow(s) (`.github/workflows/ingest.yml`), cron-based. Different cadences per source tier are worth it: fast-moving federal/aggregator sources (daily) vs. slower-moving state regulation registers (2–3x/week) — configurable per row in `source_registry`, not hardcoded.
- Entry point is a plain CLI (`scripts/run_ingestion.py --sources=<comma-list|all>`) so it can also be run locally/manually — the scheduler is not special application logic.
- Each run is wrapped by a single `scraper_runs` row; each per-source attempt within it is isolated (one adapter's exception never aborts the others) and recorded.

## 9. Error handling & source health

- Per-adapter `try/except` at the run level: log the exception, record `status='failed'` + error message for that source in the run's per-source detail table, continue to the next source.
- HTTP-level retries with exponential backoff for transient errors (timeouts, 429, 5xx); a hard cap so one flaky source doesn't stall the whole run.
- A source that fails N consecutive runs (configurable, e.g. 3) is flagged `needs_attention` in `source_registry` (surfaced prominently on the dashboard's Source Health page) — it stays *enabled* so it keeps being retried, but is visually distinct from "healthy, legitimately found 0 new records."
- Structured logging (Python `logging` with a JSON formatter) so run logs can be grepped/aggregated later; nothing fancy needed at MVP scale.

## 10. Testing strategy

- **Fixtures over live HTTP.** Every adapter test runs against saved fixture HTML/JSON/PDF captured once from the real source, not live network calls — deterministic, fast, doesn't hammer government servers during CI.
- **Unit tests**: `normalize()` per adapter (raw fixture → expected `PolicyIn`), rule-engine keyword matching (true/false positive fixtures), hashing/dedup logic, status normalization mapping.
- **Contract test**: every adapter's `normalize()` output validates against the shared `PolicyIn` schema — this is what actually keeps 50 heterogeneous sources from rotting the shared data model.
- **Integration test**: full pipeline (fetch fixtures → normalize → classify [rules only] → persist) against a throwaway SQLite DB, asserting on `is_new`/`is_updated`/`policy_changes` behavior across two simulated runs.
- **Classification tests**: prompt-response contract tested against recorded/mocked Claude responses (never live API calls in CI, to avoid cost/flakiness); a small golden set of hand-labeled ambiguous bills for offline prompt-quality iteration.

## 11. Deployment

| Environment | Database | Ingestion | Dashboard |
|---|---|---|---|
| Local dev | SQLite | run manually via CLI | `streamlit run` locally |
| Shared internal (MVP) | Postgres (managed — e.g. Render/Railway/Supabase/RDS, whatever DfG IT can provision) | GitHub Actions scheduled workflow | Streamlit Community Cloud or a small internal server, reading the same Postgres |

Secrets (DB connection string, optional `ANTHROPIC_API_KEY`, aggregator API keys) live in GitHub Actions secrets and the dashboard host's env vars — never committed. Alembic manages schema migrations so the dev SQLite and shared Postgres stay in sync as the schema evolves.

## 12. What this architecture deliberately defers

- No message queue / workers — a scheduled batch job is sufficient at this data volume (hundreds to low thousands of records per run, not a streaming problem).
- No microservices — this is one Python package with clearly separated modules, not distributed services.
- No public API/auth layer — internal tool, dashboard access controlled at the hosting layer (e.g., Streamlit Community Cloud's built-in auth or an internal network), not built into the app itself for MVP.
