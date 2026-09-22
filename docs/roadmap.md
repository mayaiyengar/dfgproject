# MVP Recommendation, Repository Structure, Metrics, and Roadmap

Status: proposed, pending approval.

## 1. MVP scope (Phase 1)

**Federal + 5 pilot states: California, Washington (State), New York, Texas, Mississippi.**

### Why these five, not the five "easiest" states

It would be tempting to pick the five friendliest sources from `source-inventory.md` (Rhode Island, Washington, Utah, Pennsylvania, Connecticut) and declare an easy win. That would validate the adapter pattern only against its best case and leave the harder ~35 states as a surprise for Phase 6. Instead, Phase 1 is chosen to deliberately span the difficulty spectrum found in research, while also matching where DfG's advocacy work actually needs coverage first:

| State | Difficulty (regs/EOs) | Why it's in Phase 1 |
|---|---|---|
| **Washington (State)** | Easy — the most scrape-friendly jurisdiction found in the entire 50-state survey (predictable Register URL pattern, clean EO HTML, Socrata open data) | Proves the "happy path": if the adapter pattern isn't clean here, it isn't clean anywhere. Cheapest state to get end-to-end first. |
| **California** | Easy (bills, via an official bulk feed independent of any aggregator) / Medium (regs & EOs) | Largest state population; `data.ca.gov` has directly relevant CDPH/CDE/CDCR datasets; CA has an active menstrual-equity legislative history worth tracking closely, and its bill infrastructure is genuinely best-in-class — good second data point after WA for the "structured official source beyond the aggregator" pattern. |
| **New York** | Easy–Medium | Second-largest legislative volume in the country; has its own **Open Legislation REST API** independent of Open States/LegiScan, which is a useful second real-world example of "a state exposing more than the aggregator gives us" (California's bulk feed is the first). Strong `data.ny.gov`. |
| **Texas** | Medium–Hard — regulations/admin code are workable, but there is **no unified executive-order index** (confirmed gap; EOs are scattered across news posts and ad hoc PDFs) | Second-largest population; forces Phase 1 to build the "no clean index, fall back to monitoring a news feed for interesting PDFs" pattern early rather than deferring it. Corrections/incarceration-related menstrual-equity policy has been a recurring topic in Texas specifically. |
| **Mississippi** | Hard — fragmented, unindexed executive orders; state open-data infrastructure appears largely absent | Genuine stress test of the architecture's PDF-heavy, low-infrastructure end. Also directly mission-relevant: Mississippi has among the highest period-poverty rates in the country and no sales-tax exemption for menstrual products — exactly the kind of state where DfG's advocacy attention (and this tool's coverage) matters most, even though it's the hardest to automate. |

This set intentionally includes one Easy, two Easy–Medium, one Medium–Hard, and one Hard jurisdiction, plus the federal layer — if the adapter base classes (`architecture.md` §3.2) hold up across all five, they'll hold up for the remaining states as configuration, not new engineering.

### What "in scope" means for Phase 1

- Federal: Federal Register (rules + EOs) and Congress.gov (bills) as the two primary sources; GovInfo, Regulations.gov, and USAspending as secondary/lower-frequency.
- Each pilot state: bill tracking via Open States (primary) with LegiScan available as a cross-check adapter; one bespoke regulation-register adapter; one bespoke executive-order adapter.
- Explicitly **out of scope for Phase 1**: ballot measures (no jurisdiction's aggregator covers these, and building a 51-jurisdiction ballot-measure adapter is its own project); federal/state agency guidance beyond the specific high-value documents named in `source-inventory.md` (e.g., BOP Program Statement 5200.07); any state not in this list.

### Before writing adapter code

Two action items from `source-inventory.md` §7 apply directly to this pilot set and should be resolved first: (1) confirm Open States' and LegiScan's terms of service permit an internal nonprofit tool's use and caching of their data, and (2) request an Open States rate-limit tier bump (the free tier's 250 requests/day is workable but tight for daily polling across 5 states + federal, let alone the eventual 51). Neither blocks starting Phase 1 development against fixtures, but both should be closed before the first live scheduled run.

### Subsequent phases (state expansion, Phase 6)

Once Phases 2–5 are validated against these five states (see the roadmap table below), expand in batches ordered by `source-inventory.md`'s difficulty tiers — Easy and Easy–Medium states first (quick wins, build momentum and coverage fast), then Medium, then the Medium–Hard/Hard tier and the states this research flagged as needing direct re-verification (Georgia, South Carolina, Vermont, DC, and the handful of `[u]` open-data-portal states). DC is a strong Phase 2-of-expansion candidate specifically because Open States/LegiScan already claim bill coverage for it at near-zero incremental cost — only its regulations/Mayor's-orders side needs new adapter work, and that couldn't be verified this session (network-blocked) so should be double-checked first.

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
│   │   ├── state/                # openstates_bills.py (parameterized per state), plus
│   │   │                        # one module per bespoke state regulation/EO adapter,
│   │   │                        # e.g. california_regulations.py, texas_executive_orders.py
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

A simple `scripts/report_metrics.py` (Phase 5/7) can compute all of these directly from the DB for a status update — no separate analytics pipeline needed at this scale.

## 4. Implementation roadmap

| Phase | What gets built | Files touched | Tested by | "Done" means |
|---|---|---|---|---|
| **0 — Research & architecture** (this phase) | Source landscape research, architecture, schema, repo layout, risk assessment | `docs/*` | Human review (you) | You approve the plan; nothing else is committed |
| **1 — Federal + N pilot states, ingestion only** | Base adapter classes; Federal Register + Congress.gov adapters; Open States/LegiScan-backed bill adapter for pilot states; per-pilot-state bespoke regulation/EO adapters; `source_registry` seeded via `config/sources.yaml`; `run_ingestion.py` prints normalized records to stdout/CSV (no DB yet, or SQLite only) | `src/ingestion/**`, `src/normalization/**`, `config/sources.yaml`, `scripts/run_ingestion.py`, `tests/ingestion/**`, `tests/fixtures/**` | Fixture-based unit tests per adapter; manual spot-check against live sources | Every pilot source produces valid `PolicyIn` records from both fixtures and one live run; no live scraping in CI |
| **2 — Database + deduplication + change detection** | SQLAlchemy models + Alembic migrations for the full schema; dedup logic; content-hash change detection; `scraper_runs`/`scraper_run_sources` bookkeeping | `src/database/**`, `src/monitoring/dedup.py`, `src/monitoring/change_detection.py`, `src/monitoring/run_logger.py`, `tests/integration/**` | Integration test running two simulated pipeline passes against SQLite, asserting `is_new`/`is_updated`/`policy_changes` correctness | Running ingestion twice in a row produces exactly the expected new/updated/unchanged counts; a run with an injected adapter failure still completes and is visibly marked failed for that source |
| **3 — Relevance classification** | Stage-1 rule engine with a curated keyword/taxonomy list (built with DfG advocacy staff's input on real examples); stage-2 optional Claude integration with structured-output validation and prompt versioning | `src/classification/**`, `tests/classification/**` | Unit tests against a hand-labeled fixture set (known-relevant / known-irrelevant / known-ambiguous bills); pipeline runs correctly with `ANTHROPIC_API_KEY` unset | Rule engine achieves an agreed-on precision/recall bar on the labeled set; pipeline runs end-to-end with zero AI configured |
| **4 — Change detection refinement + reviews workflow** | `reviews` table + basic CRUD used by dashboard; refine dedup heuristics based on real cross-source collisions seen in Phase 1–2 data | `src/database/models.py` (reviews), `src/monitoring/dedup.py` refinements | Integration tests with real captured duplicate cases | Staff can mark a policy reviewed and it's queryable; known duplicate cases from pilot data are correctly merged, not double-counted |
| **5 — Dashboard** | Streamlit app: New/Updated views, search/filter (state, level, type, category, status), policy detail with classification + review controls, source-health page, CSV/Excel export | `src/dashboard/**` | Manual walkthrough against seeded pilot data; a couple of Streamlit smoke tests | A DfG staff member can find, filter, review, and export pilot-phase policies without touching a database client |
| **6 — Expand states** | Add remaining states in priority-ordered batches (see `source-inventory.md` technical-difficulty tiers), reusing existing base adapter classes; grow `config/sources.yaml` | `src/ingestion/state/**`, `config/sources.yaml`, `tests/ingestion/**` | Same fixture-based adapter tests, one batch at a time | All 50 states + DC have at least a bill-tracking source enabled; regulation/EO coverage tracked and expanded per the difficulty tiers, not blocked on 100% coverage |
| **7 — Testing, monitoring, deployment hardening** | Full CI (GitHub Actions) running the test suite; scheduled ingestion workflow; shared Postgres provisioned; source-health alerting (e.g., a digest when a source hits `needs_attention`); `scripts/report_metrics.py` | `.github/workflows/**`, `pyproject.toml` (CI config), `scripts/report_metrics.py` | CI green on PRs; a scheduled run against the shared Postgres completes successfully end-to-end | The tool runs unattended on schedule, staff use the hosted dashboard, and a failed source shows up as a visible alert rather than silence |

Phases 1–5 should be built and validated against the pilot states before Phase 6 fans out — the whole point of the adapter pattern is that expanding state coverage becomes "write a config + a mapping function," not "redesign the pipeline," and Phase 1–5 is where that gets proven or corrected.
