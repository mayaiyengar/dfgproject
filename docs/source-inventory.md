# Source Inventory

Compiled 2026-09-22 from live research (WebSearch throughout; direct WebFetch to most `.gov`/state open-data domains was blocked by this environment's network egress proxy, so a number of entries below are marked **[unverified]** — confirmed via search-result snippets, cached pages, or (for Open States) by reading the live API source code directly, rather than by loading the government page itself in this session). **Every `[unverified]` item should get one direct human page-load before an adapter is built against it** — that's a cheap check relative to building against a guess.

No API, format, or rate limit below was invented. Where research couldn't confirm something, it's stated as unconfirmed rather than assumed.

---

## 1. Federal sources

| Source | API? | Auth | Rate limit | Format | Difficulty | Recommended use |
|---|---|---|---|---|---|---|
| **Federal Register** (federalregister.gov) | Yes, `/documents/search` | **None** | No published numeric cap ("reasonable use") | JSON, CSV, RSS | **Easy** | **Primary daily source** — rules, proposed rules, notices, and Executive Orders (filter `presidential_document_type=executive_order`) |
| **Congress.gov** (api.congress.gov) | Yes | api.data.gov key (free) | 5,000/hr | JSON/XML | Easy–Medium | Bills, resolutions, sponsors, actions; poll by `updateDate` |
| **GovInfo** (api.govinfo.gov + bulk XML) | Yes (+ keyless bulk repo) | key for API only | 36,000/hr | JSON/XML/PDF | Medium | Enacted Public Laws (`PLAW`); archival backup for pre-1994 Federal Register |
| **Regulations.gov** (api.regulations.gov v4) | Yes | api.data.gov key | 1,000/hr GET (2,000/hr on request) | JSON | Medium | Open public-comment periods/dockets — Federal Register doesn't carry comment text |
| **USAspending.gov** | Yes | **None** | Not officially published (informally generous) | JSON | Medium | New/updated HHS, CDC, USDA-FNS, ED grant awards relevant to menstrual health programs |
| Agency guidance (HHS, CDC, ED, USDA, DOJ/BOP) | No unified API — each agency runs its own portal | N/A | N/A | HTML/PDF, some RSS | Medium–Hard | Best-effort: portal scrape/diff + Federal Register agency filter + specific-document hash-watching (e.g. BOP Program Statement 5200.07, a citable stable-URL PDF) |

**whitehouse.gov is explicitly not recommended** as a primary EO source — no API, unstable permalinks across administrations. federalregister.gov (cross-verified against GovInfo's archival PDF) is the system of record.

### 1.1 Federal Register API — live verification (2026-09, implementation milestone)

Before building the adapter, this environment's egress policy was checked directly: `curl`/httpx to `www.federalregister.gov` and `WebFetch` to the same domain both return an explicit block (`EGRESS_BLOCKED` / proxy `connect_rejected`) — the same restriction the original research pass hit, now confirmed to also apply in the implementation session, not just the research one. A live request against the real API could not be made.

**What was used instead, and why it's still authoritative, not a guess:** the Federal Register API is run by the National Archives, whose backend source code is public at `github.com/usnationalarchives/federalregister-api-core` (AGPLv3 — GitHub itself is not blocked by this environment's egress policy). Rather than build the adapter from the research summary alone, the actual serializer, model, search, and controller source were read directly:
- `app/presenters/entry_api_representation.rb` — the exact field list returned per document, and the default field set for search results.
- `app/models/entry.rb` — the `ENTRY_TYPES` mapping (the real values behind the `type` field).
- `app/searches/entry_search.rb` — accepted values for the `type` filter condition.
- `app/controllers/api_controller.rb` (`render_search`) — the actual JSON response envelope and its behavior on zero results.

This is source-code-level ground truth for the current API, not the same as a live request/response pair, but a stronger form of verification than the original research pass had (which relied on search-result summaries of the docs page). It cannot substitute for a live request entirely — response *behavior* under real-world conditions (actual rate limiting thresholds, transient errors, an edge case the code doesn't obviously reveal) is still unverified. **A live smoke-test run against the real API is a pre-production action item** (see `roadmap.md`), same as the ToS action items already tracked there.

**Confirmed field names/values** (used directly in `src/ingestion/federal/federal_register.py`): `document_number`, `title`, `abstract`, `type`, `subtype`, `html_url`, `pdf_url`, `publication_date`, `signing_date`, `effective_on`, `executive_order_number`, `citation`, `agencies`. `type` values are exactly `"Rule"`, `"Proposed Rule"`, `"Notice"`, `"Presidential Document"`, `"Correction"`, `"Uncategorized Document"`, `"Sunshine Act Document"` (from the `ENTRY_TYPES` constant); the `type` *filter* condition takes the underlying codes `RULE` / `PRORULE` / `NOTICE` / `PRESDOCU` / `CORRECT`. Executive orders are `type="Presidential Document"` with `subtype="Executive Order"` (subtype is the presidential-document-type name — Proclamations, Memoranda, Determinations, and Notices share the same `type` and differ only by `subtype`).

**Discrepancies found versus the original research pass** (documented per the "stop and document" rule rather than silently built around):

1. **There is no `effective_date` field.** The real field is `effective_on`. The original research/architecture assumption of a generic `effective_date` was wrong for this specific source; the adapter uses `effective_on`.
2. **A zero-result search response omits the `results` key entirely** rather than returning `"results": []`. Confirmed directly in `api_controller.rb`'s `render_search`: the key is only added `if search.count > 0 && results.count > 0`. The adapter reads `payload.get("results", [])`, not `payload["results"]`, specifically because of this.
3. **The exact query-parameter value for filtering by `presidential_document_type`** (e.g. whether it's `executive_order`, `Executive Order`, or a numeric ID) could not be confirmed from the search/filter source in the time available — the model uses an `identifier_attribute` whose exact values live in seed data, not code. Rather than guess, the adapter fetches all `PRESDOCU` (Presidential Document) records and filters to `subtype == "Executive Order"` client-side, which *is* fully verified (§ above). This is slightly less efficient (fetches a few non-EO presidential documents per page that are then discarded) but doesn't depend on an unverified filter value.
4. **`order` parameter values** (`relevance`/`newest`/`oldest`) were not independently re-confirmed against source in this pass — `entry_search.rb` confirms three sort modes exist (labeled "Relevant"/"Newest"/"Oldest" for display) but not their exact lowercase URL parameter spelling. The adapter uses `order=newest` per the original research's citation of the public docs page; because incremental fetching relies on the `conditions[publication_date][gte]` date filter (confirmed field name) rather than result ordering, an incorrect `order` value would affect result *ordering* only, not correctness of what's fetched.
5. **Default/maximum `per_page`** was not found as a hardcoded constant in the controller; a third-party R client library's documentation states a maximum of 1000. The adapter defaults to 100 and treats 1000 as an assumed ceiling, not independently confirmed — flagged for the live smoke-test action item.

---

### 1.2 Congress.gov API — verification method (2026-09, implementation milestone)

**No live API request was made, for two independent reasons, not one:** this environment has no `CONGRESS_API_KEY` configured, and `api.congress.gov` is blocked by this environment's egress policy regardless (confirmed directly — same restriction as every other `.gov` domain tested). Per the implementation rules, neither condition was worked around and no response shape was invented to fill the gap.

**What was used instead:** the Congress.gov API is maintained by the Library of Congress at `github.com/LibraryOfCongress/api.congress.gov` (public, official). Verification drew on several files in that repo, each cited below — this is "official documentation/source-code verification," explicitly not equivalent to a live request/response pair:
- `Documentation/BillEndpoint.md` — the exact field lists for the **list** response (`congress`, `type`, `originChamber`, `originChamberCode`, `number`, `url`, `title`, `updateDateIncludingText`, `latestAction`, `updateDate`) versus the **detail** response (adds `introducedDate`, `sponsors`, `cosponsors`, `committees`, `actions`, `policyArea`, `subjects`, `summaries`, `laws`, and more).
- `Documentation/openapi.yaml` — confirms the list endpoint's query parameters are `format`, `offset`, `limit`, `fromDateTime`, `toDateTime` (the file is large enough that some deeper sections, like the full parameter schema definitions, were truncated by the fetch tool and could not be read — noted as a residual gap below).
- `python/cdg_client.py` — the official reference client's actual request-construction code.
- `python/bill_example.py` and `python/bill_example_output.txt` — a worked example script and its real recorded output.

**Confirmed facts used directly in `src/ingestion/federal/congress_gov.py`:**
- Base URL: `https://api.congress.gov/v3/`. Bill list: `GET /bill` or `GET /bill/{congress}`. Bill detail: `GET /bill/{congress}/{billType}/{billNumber}` (e.g. `bill/117/hr/21` — `billType` is lowercase, e.g. `hr`, `s`, `hjres`).
- **Authentication is a header, `X-Api-Key`, not a query parameter** — confirmed directly from `cdg_client.py`'s source: `self._session.headers.update({"x-api-key": x_api_key})`, with an explicit code comment: *"do not use url parameters, even if offered, use headers."* This is a real discrepancy against the generic api.data.gov convention (`?api_key=...`) the original research assumed by analogy with GovInfo/Regulations.gov — Congress.gov's own reference client deliberately avoids the query-param form.
- Pagination: response includes a `pagination` object with `count` and, when more pages exist, `next` (a full URL) — confirmed from `bill_example.py`'s `get_bill_pagination()` function, which loops `while "next" in pagination_info`.
- Incremental filtering: `fromDateTime` / `toDateTime` query parameters, ISO-8601 UTC format confirmed by example: `2022-01-04T04:02:00Z`. Sorting: `sort=updateDate+desc`.
- Default page size 20, adjustable up to 250 (README, matches original research).

**Discrepancies/gaps found versus the original research pass:**
1. **Auth is a header, not a query param** (above) — the single most consequential finding, since building against the wrong auth mechanism would have made every request fail with 401/403 despite a valid key.
2. **List and detail responses have materially different field sets.** The list endpoint does *not* include `sponsors` or `introducedDate` — only the detail endpoint does. This means fully satisfying "preserve sponsors when available" requires a per-bill detail fetch in addition to the list fetch, which the adapter does, capped and with per-bill failure isolation (a single bad detail fetch doesn't fail the whole run) — see the adapter's docstring.
3. **The exact JSON key nesting for `latestAction` and the top-level list/detail response envelope (`bills: [...]` vs `bill: {...}`)** was inferred from the README's stated XML/JSON structural parity and the confirmed XML pagination example, not independently re-confirmed byte-for-byte in JSON. This is flagged as unverified and should be the first thing checked in a live smoke test.
4. **The public `https://www.congress.gov/bill/{congress}th-congress/{type-slug}/{number}` URL pattern** used for `source_url` is well-established, stable public knowledge of congress.gov's own URL scheme, not something confirmed against source code this session (there was no need to invent it — it's long-documented, widely used, and not proprietary — but it's disclosed here as knowledge-based rather than source-verified, consistent with the same standard applied elsewhere in this document). The API's own `url` field (an API JSON endpoint, not a human-readable page) is preserved in `raw_payload` regardless.
5. **`official_text_url` is not populated in this milestone.** Bill full text lives behind a separate `textVersions` sub-resource this adapter doesn't call — rather than guess at a URL shape, it's left `None`, with the gap disclosed here instead of filled with an assumption.
6. **`normalized_status` mapping is deliberately minimal**: `ENACTED` when the detail response's `laws` field is a non-empty list (unambiguous), else `UNKNOWN`. The full set of `latestAction` text patterns (passed one chamber, vetoed, failed, withdrawn) was not enumerated from a real sample of values, so no attempt was made to guess a fuller mapping — `official_status` preserves the verbatim `latestAction` text regardless, so nothing is lost, just not yet categorized.

Same as Federal Register: **a live smoke test against the real API, with a real key, from a network that can reach it, is a pre-production action item** (`roadmap.md` §1.5), not a blocker for continued development.

---

## 2. State & DC legislation — the aggregator layer

Rather than 50 bill-specific scrapers, bill/resolution tracking across all 50 states + DC is handled by **one reusable adapter**:

| Source | Coverage confirmed | Auth | Rate limit (free tier) | Covers regs/EOs/ballot measures? |
|---|---|---|---|---|
| **Open States API v3** (v3.openstates.org) | 50 states + DC + Puerto Rico [verified by reading live API source] | Free API key | **10/min, 250/day** [verified from live source code — tighter than commonly assumed; request a bronze/silver tier from Plural given nonprofit status] | **No** — confirmed no regulations/EO/ballot-measure endpoint exists in the API |
| **LegiScan** (legiscan.com), secondary/cross-check | All 50 states + Congress, likely DC | Free API key | 30,000/month | **No** |

Both are bill/resolution trackers only. **Regulations, executive orders, and ballot measures are a separate problem for every jurisdiction** — see §3. Open ToS questions (redistribution/attribution terms for both aggregators could not be fetched this session — network-blocked) are tracked as action items in `roadmap.md`.

**Implementation status per 2026-09 review**: only **Open States** is built as an active adapter. LegiScan remains research-stage — not integrated, no client code written — until its terms of service are directly confirmed to permit an internal nonprofit tool's use and caching of its data (`roadmap.md` §1.4). It is documented here as the researched secondary/cross-check candidate, not as a current dependency.

---

## 3. State regulations, executive orders & open data — jurisdiction-by-jurisdiction

Bills are omitted from this table (handled by §2). `[u]` = unverified this session, needs a direct follow-up check before building.

| State | Regulations register | Governor's EOs | Open data portal | Difficulty |
|---|---|---|---|---|
| Alabama | Monthly PDF register (Alabama Administrative Monthly) | Blog-style HTML archive, PDF orders | open.alabama.gov (general) | Medium |
| Alaska | OPNS searchable notice system, HTML+PDF | HTML list, PDF orders | data.alaska.gov (Socrata) | Medium |
| Arizona | Weekly PDF register (AZ Admin. Register) | HTML list, PDF orders | data.azdhs.gov (health-only) | Medium |
| Arkansas | **Bulk data download service** (portal.arkansas.gov) — real plus | HTML list | GIS hub only | Easy–Medium |
| California | Weekly PDF/HTML notice register (OAL) | Blog-style HTML archive | **data.ca.gov** — mature, CDPH/CDE/CDCR datasets | Easy (bills)/Medium (regs,EOs) |
| Colorado | Biweekly HTML/PDF register | Per-year HTML subpages, PDF | data.colorado.gov (incl. CDPHE) | Medium |
| Connecticut | **eRegulations System** — structured browse/search + email alerts | HTML list, PDF | data.ct.gov (Socrata) | Easy–Medium |
| Delaware | Monthly HTML-navigable register | Paginated HTML blog archive | Delaware Open Data Portal (API) | Easy–Medium |
| Florida | **FLRules.org** — daily register, search + email subscription | HTML list, predictable PDF URL pattern | flhealthcharts.gov (health-only) | Easy–Medium |
| Georgia | rules.sos.ga.gov — **gap: no clear separate proposed-rules notice register** `[u]` | HTML list `[u]` | No flagship portal; OASIS (health query tool) | Medium–Hard |
| Hawaii | **Gap: no statewide register** — fragmented across ~20 agency sites, PDF | Simple HTML list, PDF | opendata.hawaii.gov (Socrata, strong) | Medium–Hard |
| Idaho | Monthly PDF Bulletin, HTML index (adminrules.idaho.gov) | Centralized HTML index (same site) | **Gap** — GIS-only, no general portal | Medium |
| Illinois | Weekly PDF register (Illinois Register) | Structured HTML, one page/EO | data.illinois.gov (Socrata; upkeep uncertain) | Medium |
| Indiana | HTML-indexed register/code (iar.iga.in.gov) | Structured HTML + PDF, fragmented old/new gov sites | Indiana Data Hub (CKAN) | Medium |
| Iowa | Biweekly PDF Bulletin, consolidates rules **and EOs** | Clean HTML list (governor.iowa.gov) | data.iowa.gov | **Easy–Medium** |
| Kansas | HTML issue pages linking PDF notices, subscription option | Fragmented across 3 sources, each HTML | **Gap** — no unified state portal | Medium |
| Kentucky | HTML-indexed monthly register (ARK) — relatively good | **Gap: no current consolidated EO index** | No general-purpose portal | Hard (EOs) / Medium (regs) |
| Louisiana | Monthly PDF register; EO email subscription list available | Two-source (DOA index + governor PDF folder) | **Gap** — no unified portal; LDH health explorer only | Medium |
| Maine | HTML rulemaking notices (free); fuller "Register" is paid LexisNexis | Clean HTML per-order (2019+) | Thin/fragmented catalog | Medium |
| Maryland | Biweekly register `[u: HTML vs PDF per-issue]`; regs.maryland.gov friendlier alternative | **Paginated, well-maintained HTML list** | **opendata.maryland.gov** — ranked #1 nationally | Easy–Medium |
| Massachusetts | Biweekly Register (PDF) + HTML CMR browsing | Clean HTML, one page/order | data.mass.gov (incl. DPH warehouse) | Medium |
| Michigan | Monthly PDF register (rules + EOs combined) | Likely structured HTML (legislature.mi.gov) | data.michigan.gov (Socrata) | Medium |
| Minnesota | Register w/ URL-addressable issues `[u: HTML vs PDF per-item]` | **Searchable full-text EO database, 1967–present** | Probable gap — GIS-leaning `[u]` | Easy–Medium |
| Mississippi | Searchable admin-rules portal, underlying filings PDF | **Gap: fragmented, no indexed current list** | **Gap** — likely absent | **Hard** |
| Missouri | Biweekly PDF register; cross-listed HTML at oa.mo.gov | **Two redundant HTML sources**, per-order pages | data.mo.gov (likely, unconfirmed `[u]`) | Easy–Medium |
| Montana | Biweekly PDF register (MAR) | **Gap: no single current consolidated index** | Unconfirmed `[u]` | Hard |
| Nebraska | rules.nebraska.gov docket `[u]`, sounds structured | HTML index → PDF | Unconfirmed `[u]` | Medium |
| Nevada | Well-organized HTML index (Nevada Register), PDF filings | Sectioned HTML by year | Unconfirmed — guessed URL didn't resolve `[u]` | Medium |
| New Hampshire | Weekly PDF register, no feed | Clean HTML list | **Gap** — GIS-only (GRANIT) | Medium–Hard |
| New Jersey | Twice-monthly register (rules+EOs bundled), PDF | **Strong, long-running HTML index (1962–present)** | Likely present, unconfirmed `[u]` | Medium |
| New Mexico | Per-part HTML (NMAC) + periodic Register | HTML list, PDF orders | Fragmented, health-only | Medium |
| New York | Weekly HTML/PDF register (NY State Register) | Paginated HTML, PDF | **data.ny.gov** — 1,400+ datasets, Socrata API; plus a real **Open Legislation REST API** for bills | Easy–Medium |
| North Carolina | Twice-monthly register + weekly HTML admin code | **One HTML page per EO** — well structured | NC OneMap (GIS only) | Medium |
| North Dakota | **Gap: no real-time register** — only quarterly PDF supplements | HTML list, PDF | **Gap** — none general-purpose | **Hard** |
| Ohio | Register of Ohio + **RuleWatch Ohio email-alert service** | HTML list, PDF | **DataOhio Portal** — 200+ datasets | Easy–Medium |
| Oklahoma | HTML per-issue register (rules+EOs combined) — convenient | **Gap: no clean standalone EO list** | data.ok.gov | Medium |
| Oregon | Monthly Bulletin (HTML/PDF); modern OARD backend not public-facing | HTML list, PDF | data.oregon.gov (corrections data restricted) | Medium |
| Pennsylvania | **pacodeandbulletin.gov** — unified weekly Bulletin+Code+EOs, email subscription | Same platform + dedicated EO index | **data.pa.gov** | Easy–Medium |
| Rhode Island | **RICR** — modern per-regulation HTML platform, email subscription | Clean HTML, consistent URL pattern | Thin general portal; strong health-only data | **Easy** |
| South Carolina | Unverified this session `[u]` — believed PDF/HTML only | Unverified `[u]` | No known unified portal | Hard (low confidence) |
| South Dakota | Weekly PDF register, predictable URL pattern | Searchable EO database, PDF docs | Financial-transparency only, not program data | Medium |
| Tennessee | HTML index → PDF register | HTML list by governor, PDF | **Gap** — GIS-only | Medium |
| Texas | HTML Register/Admin Code (some PDF) | **Gap: no unified EO archive** — scattered across news/PDF | **data.texas.gov** — robust, mandated by law | Medium–Hard |
| Utah | **Utah State Bulletin** — structured HTML, bundles EOs | Clean HTML list | **opendata.utah.gov** (Socrata) | Easy–Medium |
| Vermont | **Gap: no live register/list page located** this session `[u]` | Well-structured HTML archive | data.vermont.gov (Socrata) | Medium (regs side uncertain) |
| Virginia | Biweekly PDF register + **Regulatory Town Hall** (email/comment workflow) | Actively maintained HTML list | data.virginia.gov (API) | Easy–Medium |
| Washington | **Highly predictable HTML URL pattern** by year/issue — best in dataset | Clean HTML list | data.wa.gov (Socrata) | **Easy** |
| West Virginia | ASPX query-based search system (not flat list) | HTML list, PDF | **Gap** — no unified portal | Medium |
| Wisconsin | Weekly, LRB-maintained, clean HTML | HTML list, **governor-domain-dependent URL** (maintenance risk) | **Gap** — siloed by agency | Easy–Medium |
| Wyoming | **Queryable rules database** (ASPX modes incl. "open for comment") | HTML list | **Gap** — none general-purpose | Medium |
| Washington, D.C. | DC Register (ODAI), weekly, PDF-based `[u — network-blocked]` | Mayor's Orders, PDF by year `[u]` | **opendata.dc.gov** — mature ArcGIS Hub, full REST API | Medium (pending verification) |

---

## 4. Organized by technical difficulty (regulations + executive orders — bills are uniformly "easy" via the aggregator layer)

**Easy** — predictable structured HTML, often with subscriptions: Rhode Island, Washington (State)

**Easy–Medium** — solid HTML index + PDF documents, or a strong secondary structured system: Arkansas, California, Connecticut, Delaware, Florida, Iowa, Maryland, Minnesota, Missouri, New York, Ohio, Pennsylvania, Utah, Virginia, Wisconsin

**Medium** — workable HTML index but PDF-dependent throughout, or one structural weak point: Alabama, Alaska, Arizona, Colorado, Georgia*, Idaho, Illinois, Indiana, Kansas, Louisiana, Maine, Massachusetts, Michigan, Nebraska, Nevada, New Jersey, New Mexico, North Carolina, Oklahoma, Oregon, South Dakota, Tennessee, Vermont, West Virginia, Wyoming, Washington D.C.

**Medium–Hard / Hard** — a genuine structural gap (no current register, no EO index, or no open-data infrastructure at all): Georgia* (regs gap), Hawaii (fragmented per-agency rulemaking), Kentucky (EO gap), Mississippi, Montana (EO gap), North Dakota (no real-time register), South Carolina (unverified, believed hard), Texas (EO gap)

*Georgia appears in both bands because its regulation-register gap and unverified EO page are two separate open questions.

---

## 5. Organized by source type

| Type | Examples |
|---|---|
| Keyless open API, no auth | Federal Register, USAspending.gov |
| API with free key | Congress.gov, GovInfo, Regulations.gov, Open States, LegiScan, NY Open Legislation |
| Structured web app w/ email-alert subscription (no API, but push-capable) | CT eRegulations, FLRules.org, RI RICR, OH RuleWatch, PA Bulletin, VA Regulatory Town Hall, KS proposed-regs, LA EO listserv |
| Bulk/CSV/FTP download (state-run, no aggregator needed) | Alaska bills (CSV, 1983+), California bills (official bulk repo), Arkansas admin rules (bulk download), Minnesota bills (XML/CSV), Mississippi bills (FTP), Nebraska bills (CSV export), New Jersey bills (downloads page) |
| Query-based system requiring form submission, not flat scraping | West Virginia (ASPX registers), Wyoming (ASPX rule-search modes) |
| Static HTML index → PDF documents (the majority pattern) | Most states' regulation registers and EO pages |
| No structured source at all — best-effort only | Federal agency guidance (HHS/CDC/ED/USDA/DOJ), most whitehouse.gov content |

---

## 6. Confirmed gaps (no reliable automated access currently exists)

- **No jurisdiction** publishes a machine-readable API or RSS feed specifically for regulations or executive orders — every one of the 51 relies on HTML pages, PDFs, or (best case) an email-subscription notification.
- **Regulations, executive orders, and ballot measures are entirely uncovered by Open States/LegiScan** — this is a real, structural gap in the "one aggregator" approach, not a temporary limitation.
- **No current rulemaking register**: North Dakota (only quarterly PDF supplements), Vermont (no live register located this session — needs a follow-up crawl), Hawaii (no statewide register at all, fragmented across ~20 agencies).
- **No usable executive-order index**: Kentucky, Montana, Texas, Oklahoma (all scattered across news posts / ad hoc PDFs with no consolidated list).
- **No general-purpose state open-data portal** (health/school/corrections program data): Idaho, Kansas, Maine (thin), Tennessee, West Virginia, Wisconsin (siloed by agency), Wyoming, New Hampshire.
- **Federal agency guidance** (HHS, CDC, ED, USDA, DOJ/BOP) has no unified API anywhere — scoped as best-effort in the architecture, not core infrastructure.
- **South Carolina** could not be verified at all this session (network-blocked); treat its "Hard" rating as provisional pending a follow-up with normal browser access.
- **Terms of service for Open States and LegiScan** (redistribution/attribution rules for a nonprofit's internal tool) could not be fetched this session — this is a compliance action item to close before relying on either as a production data source (see `roadmap.md`).

## 7. Open verification items (network-blocked this session, confirm before building)

These aren't guesses presented as fact — they're flagged in the table above with `[u]` and repeated here as a checklist:
Georgia (regs register + EO page structure), Maryland (Register "Assembled.aspx" — HTML vs. embedded PDF), Minnesota/Missouri/Montana/Nebraska/Nevada/New Hampshire/New Jersey (open-data portal existence/URL), South Carolina (everything), Vermont (does a live rules register exist), Washington D.C. (all of dcregs.dc.gov / os.dc.gov / opendata.dc.gov), Open States & LegiScan Terms of Service.
