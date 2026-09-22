# Risks and Limitations

Status: proposed, pending approval. This is an honest accounting of where this system will be fragile, incomplete, or wrong — written before any code exists so expectations are set up front, not discovered after a bad run.

## 1. State-by-state source differences

The research in `source-inventory.md` confirms the brief's core assumption: state government data infrastructure varies enormously, and there is no uniform 50-state pattern to build once and reuse verbatim. Concretely:
- Regulation registers range from genuinely structured (Connecticut's eRegulations app, Rhode Island's RICR, Washington's predictable HTML URLs) to PDF-only monthly bulletins to, in a few states, **no current register at all** (North Dakota, Hawaii, and — unconfirmed — Vermont).
- Executive order tracking has confirmed **complete gaps** in at least four states (Kentucky, Montana, Texas, Oklahoma) where no single consolidated index exists — orders are scattered across news posts, press releases, and ad hoc PDF folders.
- **Mitigation**: the adapter base-class pattern (`architecture.md` §3.2) absorbs most of this variation as configuration, but the Hard-tier states genuinely require custom, higher-maintenance logic (e.g., monitoring a news feed and heuristically identifying which posts are EOs) — this is real engineering cost, not something a better abstraction eliminates. Per the 2026-09 scope revision, Phase 1 (Federal + CA + WA) deliberately stays away from the Hard tier so the pipeline itself is proven first; Phase 6 then deliberately takes on Texas (Medium–Hard, missing EO index) and Mississippi (Hard) specifically so this cost is confronted directly, on a known schedule, rather than discovered as a surprise mid-expansion in Phase 7.

## 2. Websites changing structure

Every scraped (non-API) source can silently change its HTML/PDF layout at any time, breaking an adapter without warning. Evidence this already happens in practice: Open States' own scraper repository showed multiple **active 2026-dated bug-fix branches** for North Carolina, Missouri, Illinois, and Texas during this research — a well-funded, dedicated team is continuously patching state-website breakage. Wisconsin's executive-order URL is tied to the sitting governor's own domain (`evers.wi.gov`) and will need to be re-located at the next gubernatorial transition — a predictable, schedulable breakage, not a random one.
- **Mitigation**: fixture-based adapter tests (`architecture.md` §10) catch breakage the moment a live structure diverges from what the adapter expects, but only if there's a process for periodically re-running adapters against live sources and triaging failures — this is the `needs_attention` mechanism in `source_registry`, and it requires a person to actually look at it, not just log it.

## 3. Missing APIs (structural gaps, not implementation gaps)

Several gaps found in research are not "hasn't been built yet" — they are "this data plumbing doesn't exist in the state's own systems": North Dakota's quarterly (not continuous) rule supplements, Hawaii's agency-by-agency (not statewide) rulemaking, and the complete absence of a general-purpose open-data portal in at least seven states (Idaho, Kansas, Tennessee, West Virginia, Wisconsin, Wyoming, New Hampshire). No amount of scraper engineering closes these gaps — the underlying government data simply isn't published at the granularity or frequency this tool wants. **This means coverage completeness will be structurally uneven across states, and that unevenness should be visible in the dashboard (a "confidence"/"coverage" indicator per state), not papered over.**

## 4. JavaScript-heavy sites

No jurisdiction in this research was confirmed to require JavaScript rendering for its core regulation/EO listing — most run on legacy ASP.NET/static-HTML government platforms. Two states (West Virginia, Wyoming) require **query-based ASPX search forms** rather than a flat paginated list, which is a step up in complexity from static scraping (session/form handling) without requiring a full headless browser. The `PlaywrightAdapter` base class exists for the cases this changes to — a state modernizing its site onto a JS framework — but should be treated as a last resort given its cost (slower, more fragile, harder to debug) per `architecture.md` §3.2.

## 5. PDFs

PDF is the dominant format for actual rule/order text almost everywhere — confirmed as the primary or sole format for the underlying document in the large majority of states researched, even where the index page listing them is clean HTML. Risks specific to PDF ingestion:
- Text extraction quality varies (multi-column layouts, scanned/image-based older documents, inconsistent internal structure) — expect noisier normalization than from JSON/HTML sources, and budget for occasional garbled or missed content rather than treating extraction as reliable.
- PDF parsing is the single most common ingestion method across the entire state layer (`source-inventory.md` §5) — this is not a minor edge case to handle later, it is the default case for most of the 51 jurisdictions' regulation and executive-order content.
- **Mitigation**: keep `raw_payload` (the source's original response, including a reference to the source PDF) so a parsing-quality issue discovered later can be fixed by re-running normalization against the original, not by re-scraping.

## 6. Duplicate legislation

Cross-source duplication is a real, not hypothetical, risk even without LegiScan in the picture: a bill can appear via Open States *and* via a state's own bulk feed (California, Minnesota, Nebraska, Mississippi, New Jersey, and others all have some independent bill export) simultaneously — the CA pilot state in Phase 1 is a live example of this (its official bulk feed alongside Open States). If LegiScan is added later as a cross-check source (`roadmap.md` §1.4), it introduces a third copy of the same risk and should be re-evaluated against real duplicate cases at that time, not assumed to behave like the two-source case. The dedup strategy in `architecture.md` §5 (exact match on `(source_id, external_id)`, secondary fuzzy match on normalized `(jurisdiction, bill_number, session)`) is a heuristic, not a guarantee:
- Bill-number normalization across formats ("HB 123", "H.B. 123", "House Bill 123") is a known source of near-miss failures.
- A genuinely identical bill re-introduced in a later session with a new number will *not* be caught as a duplicate — this is correct behavior (it's legally a new bill) but worth knowing so staff aren't surprised.
- **Mitigation**: track dedup false-positive/false-negative cases found during Phase 1–2 pilot testing and use them to refine the heuristic (`roadmap.md` Phase 4) — this needs real data to tune, not a one-shot design.

## 7. False positives/negatives in relevance detection

The two-stage classification design (`architecture.md` §6) is explicitly built assuming this will not be perfect:
- **Stage-1 rule-based false positives**: broad keyword matches (e.g., generic "women's health" bills, "hygiene" in unrelated contexts like food safety) will surface irrelevant records that a human has to dismiss. This is treated as an acceptable cost — over-inclusion into a human review queue is far safer than under-inclusion that silently drops something real.
- **Stage-1 false negatives are the more dangerous failure mode**: a bill that addresses menstrual equity without using any of the curated keywords (e.g., a broad "reproductive health equity" bill with a menstrual-product provision buried in section 12) will simply never reach classification. No keyword list is complete, and this is the primary argument for periodically expanding the keyword/taxonomy list based on DfG staff's domain expertise and on missed-bill reports, not a one-time setup task.
- **Mitigation**: the `reviews` table's comparison against `classifications.is_relevant` (the "human review agreement" metric in `roadmap.md`) is the mechanism to detect systematic false negatives over time, but it can only catch what a human happens to find through some other channel (news, advocacy network) and flag as "the tool missed this."

## 8. AI classification limitations

The optional Claude-based stage-2 classification (`architecture.md` §6) carries its own risks even when correctly implemented:
- It only ever sees the text supplied to it (the normalized title/description/available full text) — it cannot know things not present in that text, and the prompt is designed to make it say "insufficient information" rather than fill gaps with plausible-sounding invention, but no prompt design eliminates this risk entirely; validate against the structured schema and spot-check `reason` fields, especially early on.
- Classification quality depends heavily on how much full text is actually available per source — a source that provides only a title and one-line summary (common in some state adapters) gives the classifier much less to work with than the Federal Register's full document text, so classification confidence should be expected to vary by source, not just by policy.
- Prompt/model versioning (`prompt_version`, `model` fields) exists specifically so that a classification made in month 1 can be distinguished from one made after a prompt improvement in month 6 — without this, "the classifier got better" and "the classifier is inconsistent" are indistinguishable in the data.
- The system is designed to run correctly with **zero AI classification** (`classification_method='rule_based_only'`) — this is a deliberate fallback, not a degraded mode to avoid, since DfG's Claude.ai subscription may not include API credits.

## 9. Rate limits

Two rate limits found in research are tight enough to actively shape the ingestion design, not just something to "handle with backoff":
- **Open States free tier: 10 requests/minute, 250/day** (verified from live source code — tighter than commonly assumed from older documentation). Even Phase 1's 5 states + federal will need batched, subject-filtered queries rather than one call per state per run; the 51-jurisdiction end state will very likely require requesting a paid/nonprofit tier from Plural.
- **Regulations.gov: 1,000 requests/hour default** (2,000/hr on request) — the tightest of the federal APIs, and the reason it's scoped to lower-frequency polling in `architecture.md`.
- Every other federal source (Federal Register, GovInfo, USAspending) has generous or no published limits, but "no published limit" is not a guarantee of unlimited throughput — polite backoff is implemented regardless (`architecture.md` §9).

## 10. Scraper failures

This is the risk the project brief calls out most explicitly ("a failed scraper does not silently appear as 0 policies found"), and it's addressed structurally: per-source run results (`scraper_run_sources`, `database-schema.md`), a `consecutive_failures` counter driving a `needs_attention` flag, and per-adapter exception isolation so one failure doesn't abort a whole run. The residual risk is **process, not architecture**: the `needs_attention` flag only helps if a person actually checks the Source Health dashboard page on some cadence. A silent failure that nobody looks at is functionally identical to a silent failure with no monitoring at all — this should be an explicit part of whoever operates the tool's routine, not just a feature that exists.

## 11. Data completeness

This tool is explicitly designed to track *new* activity, not to be a complete historical archive (per the project brief). This has a specific, non-obvious consequence: **a policy that existed before the tool started monitoring a given source will never appear as "new,"** and a policy from a source not yet onboarded (48 of 50 states at the end of Phase 1; 45 after Phase 6 adds NY/TX/MS) simply isn't tracked at all — its absence from the dashboard means "not yet monitored," not "doesn't exist." This is exactly what the coverage-reporting design (`architecture.md` §12) exists to make visible rather than implicit: the Coverage page and every metrics report must show per-policy-type, per-jurisdiction coverage so "the tool shows nothing for Ohio" is never misread as "nothing is happening in Ohio" when the real answer is "Ohio isn't onboarded yet."

## 12. Legal / terms-of-service considerations

- **Open States and LegiScan's terms of service could not be directly verified in this research pass** (network-blocked) — this is a real open compliance question, not a formality, before either becomes a production dependency. Specifically to confirm: internal nonprofit use permissions, any attribution requirement, and any restriction on long-term caching/storage of their data in DfG's own database.
- **Government websites' own terms/robots.txt** vary and haven't been individually audited per-source in this research pass (that's an adapter-build-time task, not a research-phase one) — `architecture.md` §9 and the project brief's compliance principles (respect robots.txt, rate limits, descriptive User-Agent, no CAPTCHA/auth bypass) apply per-source as each adapter is built, and should be a checklist item in each adapter's PR, not a one-time global policy statement.
- **BillTrack50's paid tier** ($1,000/state/year, capped at $5,000/year nationally) and **FiscalNote/Quorum** (enterprise, quote-only) were considered and are not recommended as primary sources given DfG's budget — noted here so the reasoning isn't lost if state regulation/EO coverage gaps later make a paid tool tempting; get an actual nonprofit-rate quote before assuming affordability either way.

## 13. Research-process limitations (specific to this document set)

Worth stating plainly: this environment's network egress proxy blocked direct `WebFetch` access to the large majority of `.gov` domains and several open-data hosts during this research pass, and each research agent's WebSearch budget (200 calls) was exhausted before every question could be independently confirmed. Findings were still evidence-based (live web search results, and for Open States, direct inspection of its live API source code on GitHub) rather than invented, but every item marked `[unverified]` in `source-inventory.md` — including all of South Carolina, Washington D.C., and several states' open-data-portal URLs — should get one direct human page-load before an adapter is built against it. This is a cheap check relative to the cost of building against a wrong assumption, and is listed as an explicit action item, not a footnote to ignore.
