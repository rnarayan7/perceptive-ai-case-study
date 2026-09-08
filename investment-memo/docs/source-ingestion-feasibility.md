# Source ingestion feasibility

Research on the remaining public data sources for the memo system, scored against
the ingester we actually have: stdlib HTTP only (no third-party fetch deps), keyless
preferred, and it must run from a fresh clone with no account we would have to create.

The three built sources set the bar for "clean to ingest":

- **EDGAR** — keyless JSON submissions index + HTML-to-text via the stdlib parser.
- **ClinicalTrials.gov v2** — keyless JSON REST, paginated.
- **PubMed E-utilities** — keyless XML, two requests per run.

Anything that matches that shape is Easy. HTML scraping is Medium. A redacted PDF, a
JS-rendered SPA, a login wall, or a mandatory new account is Hard or a disqualifier.

Endpoints below were verified live on 2026-09-07 except where flagged "unverified."
Effort ratings assume the stdlib-only rule holds, which means **no PDF parser** (there
is no stdlib PDF reader, so any PDF-only source needs a third-party lib and breaks the
rule).

A constraint that shapes half of these ratings: **Kymera's assets are investigational.**
KT-474 (IRAK4 degrader, hidradenitis suppurativa / atopic dermatitis), KT-621 (STAT6
degrader, atopic dermatitis / asthma), and the oncology degraders KT-333 and KT-253 have
no drug label, no FAERS record, no Drugs@FDA application, and no Orange Book row. Those
FDA sources serve *comparator and class-precedent* analysis, not the assets themselves.

## Ranked summary

Ranked by value-to-effort under the keyless, fresh-clone constraint.

| # | Source | Access | Auth | Format | Serves | Effort |
|---|--------|--------|------|--------|--------|--------|
| 1 | EDGAR full-text search (EFTS) | REST/JSON | keyless | JSON | regulatory, discovery | Easy |
| 2 | EDGAR companyfacts / XBRL | REST/JSON | keyless | JSON | price-vs-thesis | Easy |
| 3 | openFDA (label + FAERS) | REST/JSON | keyless | JSON | MoA, safety, regulatory | Easy |
| 4 | openFDA Drugs@FDA | REST/JSON | keyless | JSON | regulatory | Easy |
| 5 | CMS Spending by Drug | REST/JSON | keyless | JSON | peak-sales, price | Easy |
| 6 | NADAC (Medicaid) | REST/JSON | keyless | JSON | price | Easy |
| 7 | bioRxiv / medRxiv + Europe PMC | REST/JSON | keyless | JSON | MoA, PoS | Easy |
| 8 | DailyMed (SPL) | REST/JSON + XML | keyless | JSON/XML | MoA, regulatory | Easy-Med |
| 9 | Orange Book | bulk file | keyless | delimited text | regulatory, LoE | Easy |
| 10 | data.cdc.gov (Socrata) | REST/JSON | keyless | JSON | peak-sales (epi) | Easy |
| 11 | PubChem PUG-REST | REST/JSON | keyless | JSON | MoA (chemistry) | Easy |
| 12 | GlobeNewswire / Business Wire RSS | RSS | keyless | XML | regulatory, catalysts | Easy-Med |
| 13 | CDC WONDER | XML POST API | keyless | XML | peak-sales (epi) | Medium |
| 14 | CMS ASP pricing files | bulk file | keyless | CSV in ZIP | price | Medium |
| 15 | AACR / ASH / ASCO abstracts | HTML scrape | keyless read | HTML/PDF | MoA, PoS | Medium |
| 16 | GLOBOCAN / IARC | bulk CSV | keyless | CSV | peak-sales (onc) | Medium |
| 17 | Company IR press pages | HTML scrape | keyless | HTML | regulatory, catalysts | Medium |
| 18 | USPTO PatentsView | REST/JSON | free key required | JSON | regulatory, IP | Medium |
| 19 | FDA CRL / approval / AdComm | JSON meta + PDF | keyless | JSON/PDF | regulatory, PoS | Hard |
| 20 | Consensus estimates (Yahoo/FMP) | REST/JSON | keyless / free key | JSON | price | Medium |
| 21 | ESMO / Annals of Oncology | HTML/PDF | member login | HTML/PDF | MoA, PoS | Hard |
| 22 | Earnings-call transcripts | HTML scrape | paywall/login | HTML | regulatory, guidance | Hard |
| 23 | IR corporate/data decks | JS SPA + PDF | keyless | PDF | MoA, peak-sales | Hard |
| 24 | Google Patents | BigQuery / scrape | GCP account | SQL/HTML | regulatory, IP | Hard |
| 25 | SEER incidence | SEER*Stat / files | signed DUA | fixed-width | peak-sales (onc) | Hard |
| 26 | IHME / GBD | web tool / files | free account | CSV | peak-sales (epi) | Hard |

## Per source

### 1. EDGAR full-text search (EFTS)
`https://efts.sec.gov/LATEST/search-index?q=...` — keyless, JSON, Elasticsearch-style
under `hits.hits[]._source`. Needs a descriptive User-Agent and staying under 10 req/s,
which our HttpClient already does. Covers filing body text from 2001 on. Undocumented, so
SEC can change it, but verified working: a query on Kymera returns its exhibit-bearing 8-Ks.
Serves regulatory and, more importantly, **discovery** — it locates the 8-K exhibits, press
releases, and keyword hits ("KT-474", "atopic dermatitis", "IRAK4") that feed the other
EDGAR-overlap sources. Effort: **Easy**, one GET plus `json.loads`.

### 2. EDGAR companyfacts / XBRL
`https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json`, plus companyconcept and
cross-company frames endpoints. Keyless JSON, same fair-access rules. This is the structured
numeric backbone the follow-ups doc already flagged as not-built: cash and equivalents,
short-term investments (runway), shares outstanding (market cap / EV math), R&D burn — the
inputs for **price-vs-thesis**, as citable tagged fields instead of scraped prose. Verified:
`companyfacts/CIK0001815442.json` returns Kymera data from FY2019 through Q2 2026 with
`CashAndCashEquivalentsAtCarryingValue`, `AssetsCurrent`, `CommonStockSharesOutstanding`.
Effort: **Easy**.

### 3. openFDA — drug labels + adverse events (FAERS)
Labels `https://api.fda.gov/drug/label.json`, events `https://api.fda.gov/drug/event.json`.
Keyless works (240 req/min, 1,000/day per IP); an optional free key (email signup, no
approval) raises the daily cap to 120,000. JSON, Elasticsearch query syntax. Serves MoA and
safety from label pharmacology and indications, plus FAERS safety signals. Effort: **Easy**.
KYMR: no records for the Kymera assets themselves; use it for the comparator class, e.g.
`label.json?search=openfda.generic_name:"dupilumab"` for the AD/asthma anchor, or FAERS for
the IRAK4 / oral-JAK class in HS.

### 4. openFDA — Drugs@FDA
`https://api.fda.gov/drug/drugsfda.json`, same client and limits as source 3. Application
type (NDA/BLA/ANDA), submission timeline, approval dates, marketing status. Serves regulatory
precedent — anchoring likely filing and review timelines against comparable approvals. Effort:
**Easy**. KYMR: no Kymera application; query precedent approvals such as
`search=products.brand_name:"Dupixent"` to bracket a review timeline.

### 5. CMS Spending by Drug (data.cms.gov)
`https://data.cms.gov/data-api/v1/dataset/{id}/data` (Medicare Part D / Part B / Medicaid
Spending by Drug). Keyless JSON, paged at 5,000 rows. Gives total spend, claim counts,
beneficiary counts, and **average spending per dosage unit** — a realized, near-net price
(post-rebate on the Medicaid file). This is the cleanest free net-price analog we have, and it
directly serves peak-sales and price. Effort: **Easy** (GET + paging). KYMR: filter the Part D
file for `Brnd_Name = Dupixent` (dupilumab, the AD/asthma comparator) or the HS biologics
(`Cosentyx`, `Humira`) to bracket net price per unit and total program spend as a peak-sales
analog.

### 6. NADAC (data.medicaid.gov)
National Average Drug Acquisition Cost, the average invoice price pharmacies pay.
`https://data.medicaid.gov/api/1/datastore/query/{id}/0` (DKAN), also mirrored as a Socrata
dataset on healthdata.gov (`https://healthdata.gov/resource/2ytm-24qe.json`). Keyless JSON on
both, updated weekly. Best free benchmark for **self-administered oral / pharmacy-dispensed**
comparators and generic price floors — the likely dispensing route for KT-474 and KT-621, so
more on-point than ASP. Effort: **Easy**. Note: dataset IDs rotate per year; the 2024 GUID is
confirmed but resolve the current one at run time.

### 7. bioRxiv / medRxiv + Europe PMC
Preprint APIs: `https://api.biorxiv.org/details/biorxiv/{interval-or-DOI}/{cursor}/json` and
the same with `medrxiv`. Keyless JSON, 30 records per page. medRxiv skews clinical (trial-
adjacent readouts, closer to KT-474/KT-621 clinical work); bioRxiv skews mechanism. The catch:
**neither has server-side keyword search** — you sweep a date interval and match client-side.
Fix that with **Europe PMC**, keyless full-text search that indexes both preprint servers plus
MEDLINE/PMC: `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=KT-621%20AND%20SRC:PPR&format=json`.
Serves MoA and PoS (early mechanism and translational data ahead of peer review). Effort:
**Easy**. Europe PMC is the recommended entry point; the native preprint APIs are the detail
fetch by DOI.

### 8. DailyMed (SPL)
`https://dailymed.nlm.nih.gov/dailymed/services/v2/` with `/drugnames.json`, `/spls.json`,
`/spls/{SETID}.xml`. Fully keyless. The index queries are JSON (Easy); the full label body is
HL7 SPL XML, so deep-section parsing is `xml.etree` work (Medium for that part). Serves MoA and
regulatory from authoritative current labeling. Same investigational caveat: use it for
comparator labels, e.g. `drugnames.json?drug_name=dupilumab` then fetch the SETID's SPL.

### 9. Orange Book
Bulk file, no API: `eobzip.zip` from the FDA Orange Book data-files page, containing
`products.txt`, `patent.txt`, `exclusivity.txt` as tilde-delimited ASCII. Keyless. Effort:
**Easy** — `urllib` download, stdlib `zipfile`, split on `~`. Serves regulatory and loss-of-
exclusivity timing (patent expiry, exclusivity) for the competitive set. Two limits for KYMR:
the Kymera small molecules are not approved, so absent; and Orange Book excludes biologics
(those are in the Purple Book), so the dupilumab-class comparators are not here either. Value
is small-molecule comparator LoE.

### 10. data.cdc.gov (Socrata)
`https://data.cdc.gov/resource/{dataset-id}.json`, SODA API with SoQL params (`$where`,
`$select`, `$limit`). Keyless; a free app token only raises the throttle. The strongest keyless
epi option for NHANES/NHIS-derived prevalence and chronic-disease indicators. Effort: **Easy**.
Honest gap for KYMR: HS and adult AD prevalence are not clean CDC series — realistically they
come from claims-database papers (JAMA Derm, JAAD; HS ~0.5-1%, adult AD ~2-5% US), which is a
literature/PDF path, not an API.

### 11. PubChem PUG-REST
`https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/property/.../JSON`. Keyless,
≤5 req/s. Clean JSON. Serves MoA-adjacent chemistry (structure, identifiers, bioassay cross-
refs). Effort: **Easy**, but **low KYMR value** — the Kymera degraders are novel and thinly
represented; useful mainly for named comparators. Lowest priority of the Easy tier.

### 12. GlobeNewswire / Business Wire RSS
Keyless RSS/Atom feeds (`https://www.globenewswire.com/rss/list`, Business Wire feed options);
the true programmatic APIs are OAuth-gated and commercial. XML, easy to parse. But the public
feeds are organized by industry/topic, not per issuer, so isolating one company means filtering
a broad "Pharmaceuticals" feed by name — noisy. **The overlap matters:** material releases are
filed as 8-K Exhibit 99.1 on EDGAR, which we already ingest, de-duplicated and per-company.
Prefer EDGAR for material news; use the wires only for non-8-K items. Effort: **Easy-Medium**.

### 13. CDC WONDER
XML-over-POST API: `https://wonder.cdc.gov/controller/datarequest/{databaseID}` (e.g. `D76`
mortality). Keyless (send `accept_datause_restrictions=true`). Both request and response are
XML; CDC asks ~1 query per 2 minutes. Key limit: the **API returns national data only** — no
sub-national geography. Effort: **Medium** — stdlib POST is fine but you hand-build a verbose
XML request and parse XML back. Weak for KYMR: WONDER covers mortality and notifiable diseases,
not chronic HS/AD prevalence.

### 14. CMS ASP pricing files
Quarterly ZIP/CSV downloads (`https://www.cms.gov/medicare/payment/part-b-drugs/asp-pricing-files`),
no API. Keyless. ASP is the realized-price benchmark for physician-administered drugs (price-
vs-thesis). Effort: **Medium** — download and unzip is stdlib, but the current quarter's file
URL changes each quarter, so you scrape the CMS page to resolve it. Secondary for KYMR since
KT-474/KT-621 are likely self-administered (NADAC fits better); use ASP for infused comparators.

### 15. AACR / ASH / ASCO abstracts
No society exposes a developer API. Each publishes abstracts as a journal supplement:
Cancer Research (AACR), Blood (ASH, plus `ash.confex.com`), JCO (ASCO, on `ascopubs.org`).
AACR and ASH abstract text is free HTML; ASCO abstract text is free but the Meeting Library
adds a free-account gate for slides/posters/video. All are per-site HTML scrapes with regular
but non-standard URL structure — Effort **Medium**. PubMed/MEDLINE does not reliably index
meeting abstracts, so Europe PMC only covers these partially; treat the journal supplement as
the source of record and Europe PMC as best-effort discovery. KYMR: relevant to the oncology
degraders (KT-333 STAT3 in r/r heme malignancies, KT-253 MDM2), e.g. an ASH abstract for
KT-333 dose-escalation, an AACR proceedings hit for KT-621 preclinical selectivity.

### 16. GLOBOCAN / IARC
Interactive web platform plus bulk CSV downloads (Cancer Today, CI5plus) at
`https://gco.iarc.who.int/`. There is an undocumented internal JSON gateway the front-end
calls, but its stability as a public endpoint is **unverified**. Keyless. Effort: **Medium**
via the official CSV path. **Low KYMR relevance** — oncology incidence only, and the Kymera
lead assets are dermatology/respiratory.

### 17. Company IR press-release pages
Per-company HTML. Kymera: `https://investors.kymeratx.com/news-events/press-releases/`, with
release detail URLs like `.../news-releases/news-release-details/{slug}` — the Q4 Inc. IR
platform signature. Keyless read. Effort: **Medium** for one covered name, worse at portfolio
scale because every issuer runs a different IR vendor with no common schema, and the pages are
JS-rendered. EDGAR 8-K is the standardized fallback for anything material. Not worth building a
general scraper for; a hand-seeded per-company URL list is the pragmatic option.

### 18. USPTO PatentsView
`https://search.patentsview.org/api/v1/` (PatentSearch API). The legacy keyless API was
**discontinued May 1, 2025**; a **free API key is now mandatory** (header `X-Api-Key`), ~45
req/min, JSON. The key is free but requires a signup, so it fails the strict "no account we
would have to create" test — not disqualifying like a paid account, but friction. Serves the
IP moat (assignee, filing/grant dates). Effort: **Medium** (key provisioning plus a nested
query DSL). KYMR: query assignee "Kymera Therapeutics" for the degrader / IRAK4 / STAT6
composition-of-matter filings behind KT-474 and KT-621.

### 19. FDA CRL / approval letters / advisory-committee materials
Mixed and mostly PDF. openFDA added a Complete Response Letter database in 2025 (metadata under
an `api.fda.gov/other/...` slug — **the exact endpoint string could not be verified**, the doc
page 403'd; confirm before coding), plus bulk PDF sets. The letters themselves are **redacted
PDFs**; approval letters and AdComm briefing docs remain HTML/PDF on fda.gov with no API.
Serves regulatory and PoS directly (why applications failed) — high analytical value, poor
mechanics. Effort: **Hard** — coverage is thin and letter content is redacted PDF, which
breaks the stdlib-only rule. No Kymera CRL exists; value would be comparator regulatory risk.

### 20. Consensus / street estimates
**Confirmed: no clean free keyless feed for per-asset (per-drug) biopharma consensus exists.**
Free sources give only company-level numbers and all have catches. Yahoo Finance unofficial
(`https://query1.finance.yahoo.com/v10/finance/quoteSummary/KYMR?modules=earningsTrend,financialData`)
is keyless but now needs a crumb-token + cookie handshake, is unstable, and its ToS bars
automated access. Financial Modeling Prep (`/api/v3/analyst-estimates/{ticker}`) needs a
mandatory free signup and gates estimate fields on the free tier (250 req/day). Refinitiv,
FactSet, Visible Alpha, Koyfin are paid enterprise. For a pre-revenue name like Kymera, company-
level consensus is near-zero noise for years and never breaks out KT-474. **This validates the
architecture doc's plan** to reconstruct an implied expectation from public data (cash, shares,
comparable-deal economics, TAM) rather than pretend a consensus number is sourced. Effort:
**Medium** and not recommended.

### 21. ESMO / Annals of Oncology
Abstracts published as Annals of Oncology supplements and on ESMO OncologyPRO, both largely
**member-login gated** (access is inconsistent; some issues free). Effort: **Hard** — login
wall plus HTML/PDF. Poor fit; reach ESMO content only when it also surfaces via Europe PMC or a
press release.

### 22. Earnings-call transcripts
No keyless feed. Free-to-read HTML sources are Motley Fool (metered, blocks downloads),
Seeking Alpha (increasingly login/Premium), MarketBeat (free but S&P-500 only, and KYMR is not
in it). IR pages host audio/JS webcasts, rarely text. Effort: **Hard** — paywalls, anti-
scraping, JS. Mitigation: the earnings *press release* (financials + pipeline update) is
reliably on EDGAR as an 8-K exhibit, and sometimes prepared remarks too, which is often enough
to substitute for the call.

### 23. IR corporate / data decks
The richest single artifact (MoA, pipeline, sometimes TAM) and the hardest to ingest. Kymera's
decks live at `https://investors.kymeratx.com/static-files/{uuid}` (Q4 Inc. platform, redirects
to a q4cdn.com PDF). Two compounding problems: discovery (the presentations index is a JS-
rendered SPA, so a stdlib GET returns an app shell, not links, and the UUID filenames are not
guessable) and parsing (PDF, no stdlib reader). Effort: **Hard**. Two shortcuts: biotechs often
file the deck as **8-K Exhibit 99.2** on EDGAR, usually as `.htm` (easier to parse than the PDF)
and reachable through EFTS (source 1) — but that is a subset, since the rolling monthly
corporate deck is usually not filed. So pair the EDGAR-exhibit overlap with a **hand-seeded
per-company deck-URL manifest** (the follow-ups doc's suggestion), seeding the resolved
q4cdn.com URL for KYMR.

### 24. Google Patents
No official REST API. Programmatic access is the BigQuery public dataset `patents-public-data`,
which needs a Google Cloud account/billing project (mandatory account, effectively
disqualifying). Scraping patents.google.com is JS-rendered, anti-bot, and against ToS. Effort:
**Hard**. Use PatentsView (source 18) instead for the same KYMR assignee landscape.

### 25. SEER incidence
The SEER REST API (`https://api.seer.cancer.gov/rest/`) serves only reference databases
(staging, definitions), **not incidence rates**. Actual incidence comes through SEER*Stat
software or research data files behind a **signed Data-Use Agreement** — a real registration
and approval step. Effort: **Hard** / near-disqualifier. And oncology only, so **low KYMR
relevance** (the lead assets are derm/respiratory).

### 26. IHME / GBD
The GBD Results Tool (`https://vizhub.healthdata.org/gbd-results/`) has excellent global
prevalence/incidence for any condition including AD and asthma, but access is via the web tool
or file downloads behind a **free IHME account**, with no clean keyless REST contract. Effort:
**Hard** on the no-account rule. Worth flagging because for AD and asthma prevalence across
countries, GBD is the natural source where CDC will not help — if the no-account rule is ever
relaxed for a manual seed, this is where the KT-621 denominators would come from.

## Recommendation

Build these next, in order. All three are Easy, keyless, JSON, and each closes a named weak
spot in the memo.

**1. EDGAR extensions: full-text search (EFTS) + companyfacts/XBRL (sources 1 and 2).**
Highest value-to-effort by a distance. Both are keyless JSON, both verified working against
Kymera (CIK 1815442), and both extend the EDGAR ingester we already have rather than adding a
new access pattern. companyfacts gives the **price-vs-thesis** numeric backbone (cash, runway,
shares) as citable tagged fields instead of scraped prose — the follow-ups doc already listed
this as the not-built numeric backbone. EFTS is the **discovery layer** that locates 8-K
exhibits (press releases, and the EX-99.2 decks) by keyword, which is what makes the press-
release and IR-deck overlap tractable without building per-site scrapers. Small, safe, high
leverage.

**2. Pricing bundle: CMS Spending by Drug + NADAC (sources 5 and 6).**
This directly fixes the **peak-sales net-price** weak spot. Both are keyless JSON GETs. CMS
Spending by Drug gives average spending per dosage unit (a realized, near-net price) for analog
drugs; NADAC gives pharmacy acquisition cost for the self-administered orals that match the
KT-474 / KT-621 dispensing route. Together they replace guessed net-price assumptions in the
peak-sales model with cited analog figures — for KYMR, the dupilumab / HS-biologic comparators.

**3. openFDA: labels + FAERS + Drugs@FDA (sources 3 and 4).**
One keyless JSON client covers three endpoints and closes the **MoA/regulatory** gap on the
comparator and precedent side: label pharmacology and indications for the drug class, FAERS
safety signals, and approval timelines from Drugs@FDA to anchor the regulatory-path section.
Low effort for broad coverage.

Two honorable mentions if there is room: **bioRxiv/medRxiv via Europe PMC (source 7)** is Easy
and feeds MoA/PoS with pre-filing mechanism data (keyless keyword search through Europe PMC);
and among the epidemiology sources, **data.cdc.gov (source 10)** is the only Easy keyless epi
option, though it is worth being honest that the KYMR-specific denominators (HS and AD
prevalence) have no clean API and will need a literature/PDF seed.

What to consciously **not** build for now: consensus estimates (no free per-asset feed —
reconstruct instead), IR-deck scraping and earnings transcripts (JS/PDF/paywall — lean on the
EDGAR 8-K overlap and a hand-seeded deck manifest), conference abstracts beyond best-effort
Europe PMC discovery (per-site HTML, and ESMO is login-walled), and the oncology epidemiology
sources (SEER/GLOBOCAN are oncology and gated, and Kymera's lead assets are not oncology).
