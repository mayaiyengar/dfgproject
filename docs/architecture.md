# Architecture

Status: proposed, pending approval. Nothing in this document has been implemented yet.

## 1. Design goals

- **Reusable adapters, not 50 scripts.** A small set of base adapter classes (REST API, RSS/Atom, HTML scrape, PDF, JS-rendered) that source-specific adapters extend by supplying config + parsing logic, not by reimplementing HTTP/retry/rate-limit handling.
- **Works with zero AI configured.** Rule-based relevance filtering is the load-bearing layer. The Claude API is an optional second-stage refinement, never a dependency for the pipeline to run.
- **No silent failure.** A source that returns 0 records because it's broken must look different in the data from a source that returns 0 records because nothing new was filed.
- **Provenance first.** Every stored policy traces to a `source_url` and (where available) an `official_text_url`, plus a recoverable version history — not just a hash that proves *something* changed (see §13).
- **Coverage is reported, never implied.** "51 jurisdictions monitored" is meaningless without saying *what* is monitored in each — legislation, regulations, executive orders, and agency guidance have wildly different coverage in practice (`source-inventory.md`). The system computes and reports coverage per policy type, from actual enabled/healthy sources, not as a single aggregate number (§12).
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
| `RestApiAdapter` | auth headers, pagination, retry/backoff, rate-limit sleep | Congress.gov, Federal Register, Regulations.gov, Open States (LegiScan later, if confirmed — §3.3) |
| `RssFeedAdapter` | feed parsing (feedparser), incremental via `pubDate` | states that publish bill/EO RSS feeds |
| `HtmlListAdapter` | fetch a listing page, follow pagination, parse via a per-source CSS/XPath selector map, fetch detail pages | state regulation registers / EO pages with static HTML |
| `PdfDocumentAdapter` | download PDF, extract text (pdfplumber), regex/heuristic field extraction | states that publish EOs/rules only as PDFs |
| `PlaywrightAdapter` | headless-browser fetch for JS-rendered pages, reused Chromium context, explicit wait conditions | the handful of state sites that are SPA/JS-only (used as a last resort — slower, more fragile, and the first thing checked when a source starts failing) |

A concrete adapter (e.g. `adapters/state/california_regulations.py`) subclasses one base and supplies: base URL(s), a field-mapping function, and any source-specific quirks. Most state regulation/EO adapters are expected to be 40–120 lines of config + mapping, not new scraping engines.

### 3.3 Two tiers of sources

1. **Aggregator-backed** — **Open States** for state/DC *bills*, Congress.gov + GovInfo for federal *bills*, Federal Register for federal *rules and executive orders*. One adapter class, parameterized per jurisdiction, covers most bill-tracking needs across all 50 states without per-state scraping.
2. **Bespoke per-jurisdiction** — state regulation registers, governors' executive order pages, and anything an aggregator doesn't cover. These are the ones that actually vary state-by-state and are why an adapter *pattern* rather than a single monolithic scraper matters.

**LegiScan is explicitly deferred, not a dependency.** The architecture does not assume LegiScan's presence anywhere in the pipeline — it is not a fallback the code silently relies on, and no LegiScan client is built in the initial implementation. This research pass could not verify LegiScan's terms of service (network-blocked) or confirm its licensing permits an internal nonprofit tool's use and long-term caching of its data. Once that's confirmed directly (see `roadmap.md` §1.4), LegiScan can be added later as an optional secondary/cross-check adapter behind its own `source_registry` row — the `RestApiAdapter` base class and the bill-normalization schema already accommodate a second bill source without any redesign, so deferring it costs nothing architecturally. Until then, Open States is the sole state-bill source, subject to its documented rate limits (`source-inventory.md` §2).

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
   - Existing row, different `content_hash` → `is_updated=true`, `last_updated_at=now()`, diff the individual normalized fields and write one `policy_changes` row per changed field (`field_changed`, `old_value`, `new_value`, `change_type` in `status_change | date_change | text_change | other`), **and** write one `policy_snapshots` row capturing the full normalized+raw content at that version (§13) — `policy_changes` alone tells a human what changed; `policy_snapshots` is what makes that change reconstructable rather than just assertable.
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
- **Integration test**: full pipeline (fetch fixtures → normalize → classify [rules only] → persist) against a throwaway SQLite DB, asserting on `is_new`/`is_updated`/`policy_changes`/`policy_snapshots` behavior across two simulated runs, and on the coverage computation (§12) returning correct per-policy-type counts for the fixture `source_registry`.
- **Classification tests**: prompt-response contract tested against recorded/mocked Claude responses (never live API calls in CI, to avoid cost/flakiness); a small golden set of hand-labeled ambiguous bills for offline prompt-quality iteration.

## 11. Deployment

| Environment | Database | Ingestion | Dashboard |
|---|---|---|---|
| Local dev | SQLite | run manually via CLI | `streamlit run` locally |
| Shared internal (MVP) | Postgres (managed — e.g. Render/Railway/Supabase/RDS, whatever DfG IT can provision) | GitHub Actions scheduled workflow | Streamlit Community Cloud or a small internal server, reading the same Postgres |

Secrets (DB connection string, optional `ANTHROPIC_API_KEY`, Open States API key, and a LegiScan key later if/when it's added — §3.3) live in GitHub Actions secrets and the dashboard host's env vars — never committed. Alembic manages schema migrations so the dev SQLite and shared Postgres stay in sync as the schema evolves.

## 12. Coverage reporting

The project brief's original framing ("monitor all 50 states + DC") creates an easy trap: reporting "51 jurisdictions monitored" implies comprehensive coverage of every policy type, when in practice a jurisdiction might have a working bill-tracking adapter and nothing else. `source-inventory.md` confirms this isn't hypothetical — regulations, executive orders, and open-data infrastructure vary enormously in what's even *possible* to automate per state. The system is designed so this unevenness is visible, not smoothed over.

**Definition of a "healthy" source** (the basis for every coverage number below): a `source_registry` row is counted as contributing coverage only if `enabled=true` AND `consecutive_failures` is below the `needs_attention` threshold (§9) AND it has at least one recorded successful run (`last_checked_at` is not null). A configured-but-never-successfully-run source, or one that's currently flagged `needs_attention`, does not count — coverage reflects sources that are actually working, not sources that merely exist in config.

**Coverage computation**, per `policy_type` (legislation, regulation, executive_order, guidance, appropriation, ballot_measure, administrative_action):

```
coverage(policy_type) = count(distinct jurisdiction)
                         from source_registry
                         where policy_type = ANY(policy_types_covered)
                         and <healthy, as defined above>
                         -- reported as "X/51" for state+DC jurisdictions,
                         -- and separately as covered/not-covered for federal,
                         -- since federal is a single jurisdiction, not a fraction
```

This is implemented once, as a single function (e.g. `src/monitoring/coverage.py:compute_coverage()`), and called from both the dashboard's Coverage page and `scripts/report_metrics.py` — never recomputed independently in two places, which is exactly how a dashboard number and a status-report number drift apart and one of them quietly becomes wrong.

**What the dashboard must show**: a Coverage page presenting a table like:

| Policy type | Federal | States + DC | Jurisdictions covered |
|---|---|---|---|
| Legislation | ✅ | 2/51 | CA, WA |
| Regulations | ✅ | 2/51 | CA, WA |
| Executive orders | ✅ | 2/51 | CA, WA |
| Agency guidance | Partial (best-effort) | 0/51 | — |
| Appropriations/funding | ✅ | 0/51 | — |
| Ballot measures | — (N/A federally) | 0/51 | — |

**What the dashboard must never show**: a single headline figure like "51 states monitored" with no breakdown, or a coverage count that includes disabled/unhealthy sources. This isn't a UI nicety — it's the difference between DfG staff correctly understanding "we track legislation in 2 states so far" versus incorrectly believing "this tool watches everything happening in all 50 states," which the underlying data plainly does not support and never claims to in the acceptance criteria (`roadmap.md` §1.5, item 10).

## 13. Provenance & retention strategy

**The problem with hash-only change detection**: §7's `content_hash` reliably tells you *that* a policy changed and, combined with `policy_changes`, *which fields* changed — but neither lets you reconstruct what the record actually looked like before the change, or recover the original source document if a field-mapping bug is later discovered. A hash is a fingerprint, not a backup.

**Recommendation: two retention tiers, split by cost.**

1. **Text/metadata snapshots — always retained, cheap.** Every time change detection (§7) detects a `content_hash` change, write a full snapshot of the normalized fields plus the source's raw metadata response to a new append-only `policy_snapshots` row (schema in `database-schema.md`), keyed by `(policy_id, content_hash)`. This is plain text/JSON — at the data volumes this project expects (hundreds to low thousands of policies per year across the pilot and expansion phases, not millions), the storage cost of keeping every version indefinitely is negligible (low tens of KB per snapshot). This tier fully resolves the "hash alone" problem: any prior version of any policy can be reconstructed exactly, not just diffed field-by-field.
2. **Source binaries (PDFs, etc.) — content-addressed, deduplicated, retained by policy.** The actual source document (a PDF register issue, a scanned EO) is the expensive part to keep, especially since many revisions of a policy re-fetch the *same* underlying document unchanged. Rather than storing a fresh copy in every snapshot: compute a `sha256` of the fetched binary, use that hash as a content-addressed key in a cheap object store (local disk in dev, S3-compatible storage in production), and store only the hash + a reference path in `policy_snapshots.raw_document_ref` — never the binary itself in Postgres. Storing by content hash means a PDF that's fetched 50 times unchanged across 50 runs is stored exactly once. If storage growth ever becomes a real concern (unlikely at this project's scale, but worth having an answer for), a lifecycle policy can prune binaries for versions older than N-most-recent per policy while *always* keeping the text/JSON snapshot — the recoverability that actually matters for auditing "what did this policy say" survives even if the original PDF bytes are eventually cleared.

**What this deliberately avoids**: embedding raw binary blobs in the primary Postgres database (bloats backups, slows queries, and mixes transactional and blob-storage concerns), and re-fetching from `source_url` as a substitute for retention (`risks-and-limitations.md` notes government sites regularly restructure URLs and can pull down or replace documents — the source is not a reliable long-term archive of what it published last month).

`policies.raw_payload` (§ persistence) keeps only the *latest* raw response for quick debugging convenience; the full historical record — every version, every source document reference — lives in `policy_snapshots`.

## 14. What this architecture deliberately defers

- No message queue / workers — a scheduled batch job is sufficient at this data volume (hundreds to low thousands of records per run, not a streaming problem).
- No microservices — this is one Python package with clearly separated modules, not distributed services.
- No public API/auth layer — internal tool, dashboard access controlled at the hosting layer (e.g., Streamlit Community Cloud's built-in auth or an internal network), not built into the app itself for MVP.
