# MVP Recommendation, Repository Structure, Metrics, and Roadmap

Status: proposed, pending approval.

## 1. MVP scope (Phase 1)

**Revised per 2026-09 review: Federal + California + Washington (State) only.** New York, Texas, and Mississippi move to the next phase (§1.3 below), after the full pipeline is proven end-to-end on a smaller, lower-risk slice.

### 1.1 Why Federal + CA + WA, and why smaller than originally proposed

The original plan proposed five pilot states chosen to span the whole difficulty spectrum (Easy through Hard) in one pass. On review, that front-loads risk: it means debugging adapter-pattern issues, dedup issues, and classification issues *simultaneously* with the hardest sources in the dataset (Texas's missing EO index, Mississippi's fragmented low-infrastructure sources). The revised Phase 1 goal is narrower and more disciplined — **prove the complete pipeline end-to-end, reliably, on a manageable slice, before adding difficulty:**

> source → fetch → parse → normalize → deduplicate → relevance detection → database → change detection → dashboard/export

| Jurisdiction | Difficulty (regs/EOs) | Why it's in this first slice |
|---|---|---|
| **Federal** | Easy (Federal Register, no auth) / Easy–Medium (Congress.gov) | The best-documented, most stable APIs found in any part of the research — lowest-risk way to validate the REST-API adapter base class and the federal side of normalization. |
| **Washington (State)** | Easy — the most scrape-friendly jurisdiction found in the entire 50-state survey (predictable Register URL pattern, clean EO HTML, Socrata open data) | Proves the HTML/PDF-adapter path on the friendliest possible target. If the pattern isn't clean here, it isn't clean anywhere — this isolates pipeline bugs from source-difficulty noise. |
| **California** | Easy (bills, via an official bulk feed independent of any aggregator) / Medium (regs & EOs) | Adds a second, deliberately less-tidy state (Medium-difficulty regs/EOs, not another Easy one) so Phase 1 validates the adapter pattern against more than a single best-case source, without yet taking on a Hard-tier jurisdiction. Largest state population; `data.ca.gov` has directly relevant CDPH/CDE/CDCR datasets; active menstrual-equity legislative history worth tracking closely. |

This is intentionally **not** a difficulty-spanning set. Stress-testing the Medium–Hard and Hard tiers is deliberately deferred to §1.3, once the core pipeline (dedup, change detection, classification, dashboard, export) is proven and stable against a smaller, mostly-friendly slice.

### 1.2 What "in scope" means for this phase

- Federal: Federal Register (rules + EOs) and Congress.gov (bills) as the two primary sources; GovInfo, Regulations.gov, and USAspending are lower priority and can be deferred within Phase 1 if time-constrained.
- CA and WA: bill tracking via **Open States only** (see §1.4 — LegiScan is explicitly not built in this phase); one bespoke regulation-register adapter per state; one bespoke executive-order adapter per state.
- Explicitly **out of scope for this phase**: ballot measures; federal/state agency guidance beyond the specific high-value documents named in `source-inventory.md` (e.g., BOP Program Statement 5200.07); NY, TX, MS, and every other state; LegiScan integration.

### 1.3 Next phase: expand to NY, TX, MS (Phase 6)

Once Phases 2–5 are built and validated against Federal + CA + WA (acceptance criteria in §1.5 all passing on real, not just fixture, data), the next expansion phase adds exactly the three states originally paired with Phase 1, now deliberately sequenced to stress-test progressively harder sources once the pipeline itself is no longer the variable being tested:

| State | Difficulty (regs/EOs) | Why it's next |
|---|---|---|
| **New York** | Easy–Medium | Second-largest legislative volume in the country; has its own **Open Legislation REST API** independent of Open States, a useful second real-world example of "a state exposing more than the aggregator gives us" (California's bulk feed is the first). Lowest-risk of the three additions — good first expansion target. |
| **Texas** | Medium–Hard — regulations/admin code are workable, but there is **no unified executive-order index** (confirmed gap; EOs are scattered across news posts and ad hoc PDFs) | Second-largest population; introduces the "no clean index, fall back to monitoring a news feed for interesting PDFs" pattern once the core pipeline is already trustworthy. Corrections/incarceration-related menstrual-equity policy has been a recurring topic in Texas specifically. |
| **Mississippi** | Hard — fragmented, unindexed executive orders; state open-data infrastructure appears largely absent | The stress test of the architecture's PDF-heavy, low-infrastructure end, taken on once Federal+CA+WA has already validated everything else. Directly mission-relevant: Mississippi has among the highest period-poverty rates in the country and no sales-tax exemption for menstrual products — exactly the kind of state where DfG's advocacy attention (and this tool's coverage) matters most, even though it's the hardest to automate. |

After NY/TX/MS, further expansion (Phase 7) proceeds in batches ordered by `source-inventory.md`'s difficulty tiers — remaining Easy and Easy–Medium states first, then Medium, then the remaining Medium–Hard/Hard tier and the states research flagged as needing direct re-verification (Georgia, South Carolina, Vermont, DC, and the handful of `[u]` open-data-portal states). DC is a strong early Phase 7 candidate since Open States already claims bill coverage for it at near-zero incremental cost — only its regulations/Mayor's-orders side needs new adapter work, and that couldn't be verified this session (network-blocked) so should be double-checked first.

### 1.4 LegiScan: deferred, not built

The original architecture treated Open States and LegiScan as a primary/secondary pair from day one. Per review, **this phase builds only the Open States adapter.** LegiScan is not integrated, and no LegiScan client code is written, until its API access, licensing, and terms of use have been directly confirmed (this research pass could not fetch legiscan.com's terms of service — network-blocked; see `source-inventory.md` §6–7). Once confirmed, LegiScan can be added as an optional secondary/cross-check adapter behind its own `source_registry` row and feature flag — it is not a blocking dependency for Phase 1 or Phase 6, and the architecture does not assume its presence anywhere in the pipeline (`architecture.md` §3.3).

### 1.5 Acceptance criteria — Federal + CA + WA

These define "done" for this phase — all must pass before moving to Phase 6 (NY/TX/MS expansion). Each is objective and tied to a concrete schema element or test, not a subjective judgment call.

1. **Fetch succeeds per source.** Each of the initial adapters — Federal Register, Congress.gov, Open States (CA + WA bills), CA regulations, CA executive orders, WA regulations, WA executive orders — successfully fetches and parses recorded fixture data with zero unhandled exceptions, and has been run at least once against the live source with a successful result recorded in `scraper_run_sources`.
2. **Failed sources are distinguishable from zero-result sources.** A simulated adapter failure (e.g., a fixture that raises inside `normalize()`) produces a `scraper_run_sources` row with `status='failed'` and a populated `error_message`; a source that legitimately finds nothing new produces `status='success', records_found=0`. These two cases must never be visually or programmatically indistinguishable on the Source Health dashboard page.
3. **Policies normalize into the common schema.** Every fetched record from every Phase 1 source validates against the shared `PolicyIn` Pydantic model with no missing required field, for both fixture and live data.
4. **Duplicate detection.** Re-running ingestion against the same upstream data a second time produces zero new `policies` rows for records already ingested — enforced by the `(source_id, external_id)` unique constraint and confirmed by an integration test.
5. **Re-running unchanged data does not create duplicates or spurious changes.** Three consecutive ingestion runs against literally unchanged fixture data leave `policies`, `policy_changes`, and `policy_snapshots` row counts identical after the first run.
6. **A modified policy creates a policy-change record.** A fixture representing a changed version of a previously-ingested record (e.g., status or date changed) produces exactly one new `policy_changes` row with correct `field_changed`/`old_value`/`new_value`, sets `is_updated=true`, and produces a new `policy_snapshots` row (see `database-schema.md`).
7. **Relevant/non-relevant classification works without AI.** With `ANTHROPIC_API_KEY` unset, every ingested record receives a stage-1 `classifications` row (`classification_method` in `rule_based`/`rule_based_only`); a hand-labeled fixture set of known-relevant and known-irrelevant records is classified correctly by the rule engine.
8. **AI classification is optional, never required.** The identical test suite passes both with `ANTHROPIC_API_KEY` unset (classification falls back to `rule_based_only`) and set (ambiguous candidates get an `ai_assisted` row with `model`, `prompt_version`, and a `reason` grounded in supplied text). No test or pipeline path requires the key to be present.
9. **Official source URLs are preserved.** Every `policies` row has a non-null `source_url`; `official_text_url` is populated for every CA/WA regulation and executive-order record where the source provides direct text access.
10. **Coverage is reported honestly, not implied.** Running the coverage computation (`architecture.md` §12) against the Phase 1 `source_registry` produces per-policy-type counts (e.g., "Legislation: 2/51 + federal", "Regulations: 2/51 + federal", "Executive orders: 2/51 + federal", "Ballot measures: 0/51") — it must never report a single aggregate "states monitored" number that overstates coverage.
11. **Dashboard filters work.** Jurisdiction, level, policy-type, and status filters each return correct results against seeded Phase 1 data, individually and combined, verified by Streamlit smoke tests.
12. **CSV export works.** Exporting the current filtered dashboard view produces a CSV with the expected columns (including `source_url`) and the correct row count.
13. **Tests run without live government dependencies.** The full adapter, normalization, classification, and persistence test suite passes in CI with network access disabled, using only recorded fixtures — no test calls a live government website or the Open States/Congress.gov/Federal Register APIs.

## 2. Repository structure

```
dfgproject/
├── docs/                        # this planning doc set
│   ├── architecture.md
│   ├── database-schema.md
│   ├── source-inventory.md
│   ├── risks-and-limitations.md
│   └── roadmap.md
├── src/
│   ├── ingestion/
│   │   ├── base/                # RestApiAdapter, RssFeedAdapter, HtmlListAdapter,
│   │   │                        # PdfDocumentAdapter, PlaywrightAdapter
│   │   ├── federal/              # congress_bills.py, federal_register.py, govinfo.py,
│   │   │                        # usaspending.py, regulations_gov.py
│   │   ├── state/                # openstates_bills.py (parameterized per state; LegiScan
│   │   │                        # NOT implemented until its ToS/access are confirmed —
│   │   │                        # see roadmap.md §1.4), plus one module per bespoke
│   │   │                        # state regulation/EO adapter, e.g. california_regulations.py,
│   │   │                        # washington_executive_orders.py (Phase 1); texas_executive_orders.py
│   │   │                        # etc. added in Phase 6
│   │   └── registry.py          # loads source_registry, dispatches to adapters
│   ├── normalization/
│   │   ├── schema.py            # PolicyIn / canonical pydantic models
│   │   ├── status_mapping.py    # per-source status → normalized_status
│   │   └── hashing.py           # content_hash computation
│   ├── classification/
│   │   ├── rules.py             # stage-1 keyword/taxonomy engine
│   │   ├── prompts/              # versioned prompt templates for stage-2 AI classification
│   │   ├── claude_client.py     # optional; no-ops cleanly if no API key configured
│   │   └── schema.py            # structured-output validation models
│   ├── database/
│   │   ├── models.py             # SQLAlchemy models matching database-schema.md
│   │   ├── session.py
│   │   └── migrations/           # Alembic
│   ├── monitoring/
│   │   ├── pipeline.py           # orchestrates fetch → normalize → classify → persist per run
│   │   ├── change_detection.py
│   │   ├── dedup.py
│   │   └── run_logger.py         # writes scraper_runs / scraper_run_sources
│   ├── dashboard/
│   │   └── app.py                # Streamlit entrypoint + pages/
│   ├── models/                   # shared dataclasses/enums used across layers
│   │   └── enums.py               # policy_type, normalized_status, categories, etc.
│   └── utils/
│       ├── http.py               # shared requests/httpx session w/ retry, UA, rate limiting
│       └── config.py              # env var / settings loading
├── scripts/
│   ├── run_ingestion.py          # CLI entrypoint used by both GitHub Actions and local runs
│   └── seed_source_registry.py   # loads source_registry from a config file
├── config/
│   └── sources.yaml               # declarative source_registry seed data
├── tests/
│   ├── fixtures/                  # saved HTML/JSON/PDF per adapter, no live HTTP in CI
│   ├── ingestion/
│   ├── normalization/
│   ├── classification/
│   └── integration/
├── .github/
│   └── workflows/
│       └── ingest.yml             # scheduled ingestion run
├── alembic.ini
├── pyproject.toml
└── README.md
```

Rationale for a couple of deviations from the brief's suggested layout: `models/` is kept small (shared enums/dataclasses only) since the actual ORM models live in `database/models.py` next to the session/migration code that owns them — splitting SQLAlchemy models away from their migrations tends to cause drift. `config/sources.yaml` + `scripts/seed_source_registry.py` exist so `source_registry` rows are declared in version control (reviewable in a PR) rather than only editable by hand in the database.

## 3. Measurable metrics

All of these are computed from real data the schema already captures — nothing here is a number to report until the system has actually run.

| Metric | Computed from |
|---|---|
| Jurisdictions monitored | `count(distinct jurisdiction)` in `source_registry` where `enabled` |
| Sources monitored (and by type) | `count(*)` from `source_registry` grouped by `source_type`, `enabled` |
| Policies processed (cumulative / per run) | `count(*)` from `policies`; `scraper_runs.records_found` per run |
| Relevant policies identified | `count(*)` from `policies` joined to latest `classifications` where `is_relevant=true` |
| New policies detected (per period) | `count(*)` from `policies` where `first_seen_at` in period |
| Policy updates detected (per period) | `count(*)` from `policy_changes` where `detected_at` in period, grouped by `change_type` |
| Source success/failure rate | `scraper_run_sources.status` distribution over trailing N runs, per source |
| Average scraper runtime (overall + per source) | `avg(duration_seconds)` from `scraper_run_sources`; `avg(finished_at - started_at)` from `scraper_runs` |
| Human review agreement with classifier | join `reviews.review_status` to the `classifications` row current as of `reviewed_at`; agreement = matching `confirmed_relevant`/`confirmed_not_relevant` vs. classifier's `is_relevant` |
| Time-to-detection (bonus, not in original list) | `first_seen_at - introduction_date` (or `last_action_date` for regs/EOs) — measures how quickly the tool catches something after the government publishes it, which is arguably the metric DfG advocacy staff care about most |
| **Coverage, by policy type** (added per 2026-09 review — see `architecture.md` §12) | For each `policy_type`, `count(distinct jurisdiction)` with an enabled, healthy `source_registry` row whose `policy_types_covered` includes it, divided by 51 (states + DC) — reported separately per type (legislation, regulations, executive orders, guidance, appropriations, ballot measures), never as one aggregate "states monitored" figure |

Coverage is deliberately **not** a single "N states monitored" headline number. `architecture.md` §12 defines the exact computation; every report generated from this schema (dashboard page, `report_metrics.py` output, or a status update to DfG leadership) must break coverage out by policy type, since a jurisdiction having a working bill-tracking adapter says nothing about whether its regulations or executive orders are covered.

A simple `scripts/report_metrics.py` (Phase 5/7) can compute all of these directly from the DB for a status update — no separate analytics pipeline needed at this scale.

## 4. Implementation roadmap

| Phase | What gets built | Files touched | Tested by | "Done" means |
|---|---|---|---|---|
| **0 — Research & architecture** (this phase) | Source landscape research, architecture, schema, repo layout, risk assessment | `docs/*` | Human review (you) | You approve the plan; nothing else is committed |
| **1 — Federal + CA + WA, ingestion only** | Base adapter classes; Federal Register + Congress.gov adapters; **Open States only** (no LegiScan) bill adapter for CA + WA; CA/WA bespoke regulation and executive-order adapters; `source_registry` seeded via `config/sources.yaml`; `run_ingestion.py` prints normalized records to stdout/CSV (no DB yet, or SQLite only) | `src/ingestion/**`, `src/normalization/**`, `config/sources.yaml`, `scripts/run_ingestion.py`, `tests/ingestion/**`, `tests/fixtures/**` | Fixture-based unit tests per adapter; manual spot-check against live sources | Every Federal/CA/WA source produces valid `PolicyIn` records from both fixtures and one live run; no live scraping in CI |
| **2 — Database + deduplication + change detection + retention** | SQLAlchemy models + Alembic migrations for the full schema, **including `policy_snapshots`**; dedup logic; content-hash change detection; `scraper_runs`/`scraper_run_sources` bookkeeping; snapshot-writing logic on every detected change (`architecture.md` §13) | `src/database/**`, `src/monitoring/dedup.py`, `src/monitoring/change_detection.py`, `src/monitoring/run_logger.py`, `src/monitoring/snapshots.py`, `tests/integration/**` | Integration test running two simulated pipeline passes against SQLite, asserting `is_new`/`is_updated`/`policy_changes`/`policy_snapshots` correctness | Running ingestion twice in a row produces exactly the expected new/updated/unchanged counts; a run with an injected adapter failure still completes and is visibly marked failed for that source; a changed fixture produces a reconstructable prior version via `policy_snapshots` |
| **3 — Relevance classification** | Stage-1 rule engine with a curated keyword/taxonomy list (built with DfG advocacy staff's input on real examples); stage-2 optional Claude integration with structured-output validation and prompt versioning | `src/classification/**`, `tests/classification/**` | Unit tests against a hand-labeled fixture set (known-relevant / known-irrelevant / known-ambiguous bills); pipeline runs correctly with `ANTHROPIC_API_KEY` unset | Rule engine achieves an agreed-on precision/recall bar on the labeled set; pipeline runs end-to-end with zero AI configured |
| **4 — Change detection refinement + reviews workflow** | `reviews` table + basic CRUD used by dashboard; refine dedup heuristics based on real cross-source collisions seen in Phase 1–2 data | `src/database/models.py` (reviews), `src/monitoring/dedup.py` refinements | Integration tests with real captured duplicate cases | Staff can mark a policy reviewed and it's queryable; known duplicate cases from pilot data are correctly merged, not double-counted |
| **5 — Dashboard + coverage reporting** | Streamlit app: New/Updated views, search/filter (jurisdiction, level, type, category, status), policy detail with classification + review controls, source-health page, **coverage page reporting X/51 per policy type** (`architecture.md` §12), CSV/Excel export | `src/dashboard/**`, `src/monitoring/coverage.py` | Manual walkthrough against seeded Federal/CA/WA data; Streamlit smoke tests per filter and for the coverage page | A DfG staff member can find, filter, review, and export Phase 1 policies without touching a database client; the coverage page never implies broader coverage than what's actually enabled |
| **6 — Expand to NY, TX, MS** | Bill adapters (Open States, same pattern) plus bespoke regulation/EO adapters for New York, Texas, and Mississippi — see §1.3; evaluate whether LegiScan's ToS now permit adding it as a secondary cross-check source | `src/ingestion/state/**`, `config/sources.yaml`, `tests/ingestion/**` | Same fixture-based adapter tests; acceptance criteria in §1.5 re-run against the expanded `source_registry` | NY, TX, and MS each have at least a bill-tracking source enabled, and Texas's/Mississippi's harder gaps (no EO index, fragmented sources) have a working, tested fallback adapter, not a skipped source |
| **7 — Expand remaining states** | Add remaining ~45 states + DC in priority-ordered batches (see `source-inventory.md` technical-difficulty tiers), reusing existing base adapter classes; grow `config/sources.yaml` | `src/ingestion/state/**`, `config/sources.yaml`, `tests/ingestion/**` | Same fixture-based adapter tests, one batch at a time | All 50 states + DC have at least a bill-tracking source enabled; regulation/EO/other coverage tracked and expanded per the difficulty tiers and reported per §12 coverage rules — not blocked on, or overstated as, 100% coverage |
| **8 — Testing, monitoring, deployment hardening** | Full CI (GitHub Actions) running the test suite; scheduled ingestion workflow; shared Postgres provisioned; source-health alerting (e.g., a digest when a source hits `needs_attention`); `scripts/report_metrics.py` (including coverage-by-type output) | `.github/workflows/**`, `pyproject.toml` (CI config), `scripts/report_metrics.py` | CI green on PRs; a scheduled run against the shared Postgres completes successfully end-to-end | The tool runs unattended on schedule, staff use the hosted dashboard, and a failed source shows up as a visible alert rather than silence |

Phases 1–5 must be built and validated against Federal + CA + WA — with every acceptance criterion in §1.5 passing on real (not just fixture) data — before Phase 6 adds NY/TX/MS, and Phase 6 must likewise be stable before Phase 7 fans out to the remaining states. The whole point of the adapter pattern is that expanding coverage becomes "write a config + a mapping function," not "redesign the pipeline" — and each phase boundary here is a checkpoint to confirm that's still true before taking on more jurisdictions.
