# Research Workstation — Web App Build Spec

Status: draft for handoff. This is the brief for building a fully functioning web app on top of the existing Stage 1 (figure extraction) and Stage 2 (investment memo) engines, matching the Figma mocks.

Stack decision (locked): **Next.js frontend + FastAPI JSON API**, with the existing Python packages kept as the compute layer and **Postgres as the single system of record**.

Decisions locked: Next on **Vercel**; FastAPI + worker on **Render**; **Postgres** (managed) + object storage for blobs; price from **Databento**; shares outstanding from **EDGAR** XBRL (already ingested), market cap = price × shares; consensus reconstructed (no feed for now); on-demand memo generation; auth via Vercel password protection to start, Auth.js + Google SSO later; brand "Perceptive Research OS"; the five companies' ingestion has already been run (migrate that data in).

Figma: https://www.figma.com/design/QFeDpVxHdvP1ZzUwfCWgFx — six screens across three sections (Companies, Data, Activity), plus a Foundations page with the tokens reproduced in Appendix A.

---

## Lean v1 scope (supersedes the fuller architecture where noted)

To ship a working v1 with minimal moving parts. The heavier options in §2–§7 remain the documented scale-up path.

- **Datastore:** SQLite (reuse `ledger.db`) on Render's persistent disk, not managed Postgres. The §4.2 schema applies as SQLite DDL (INTEGER PK for `bigserial`, TEXT/JSON for `jsonb`, TEXT ISO for timestamps).
- **Market data:** Alpha Vantage behind `MarketDataProvider` — `GLOBAL_QUOTE` for price, `OVERVIEW` for market cap + shares (§7.4). Key in `.env` as `ALPHAVANTAGE_API_KEY`. Replaces Databento + EDGAR.
- **Figures:** curated per company, precomputed **offline**. A one-time script runs the figure-extraction engine over a handful of each company's real figures and writes figure + extraction + doc-link rows into the app DB. The backend imports only `memo`; the figure-extraction packages are **not** a request-time dependency, so the two-package import/collision problem is out of scope for v1.
- **Object storage:** skipped. FastAPI serves images from the Render disk (as the old Flask route did).
- **Search:** deferred. The Data library filters cover browsing; the global search bar is a later add.
- **Still required:** the compose persistence refactor (0b) and packaging `memo` (0a).

---

## 1. What we are building

An internal research workstation for biotech analysts and PMs. Three nav sections:

- **Companies** — a coverage dashboard across the five names (ABVX, KYMR, PRAX, IMVT, COGT), and a per-company page with the memo, the four-question metric panels, and an audit drawer where any claim opens to its source evidence and annotated figure.
- **Data** — one filterable library of every ingested document, with a Documents/Figures toggle. Figures are elements of documents; each figure links up to its parent document and down to the claims it backs.
- **Activity** — an analyst-trust view of what the autonomous system did: ingestion runs and thesis generations, with statuses, freshness, and refusals surfaced.

The atomic unit everywhere is an auditable claim: a value with a method, a confidence, and a link back to its source. That primitive is shared between the memo (a sentence) and a figure read (a number off a chart), which is what makes Stage 1 and Stage 2 read as one product.

Non-technical framing is a hard constraint: no eval scores, token counts, latency, or model names in the UI. The only machine-confidence surfaced is the high/medium/low tier and, on a figure, the interval and dual-read agreement.

---

## Gaps and required pre-work (read before building)

The Figma is complete and the API/schema below are sound, but a review of the two packages found that several things the screens need are **not produced by the engine today**. Do the Tier 0 items before fanning out builders; they are shared foundations every screen otherwise trips on.

**Root cause.** The rendered-memo JSON (`compose/engine.py:605`) is **prose-only** (per section: prose + citations + figures). Every structured value the modules compute, per-module confidence, per-section summary, module notes, and most numeric claim values, is dropped before it reaches the ledger or the artifact. So the dashboard dots, KPIs, peak-sales number, rNPV, and metric takeaways have **no structured source today**. This is the single biggest gap and it drives half the list.

### Tier 0 — blockers (do first)
- **Package both trees.** Neither `investment-memo/` nor `figure-extraction/` has `pyproject.toml`; both import by bare top-level names (`memo`/`app`, `evaluation`/`corpus`) assuming different working dirs, with a name-collision risk. A single FastAPI process **cannot import both as-is**. Add packaging + `pip install -e` (namespace to avoid the `app`/`corpus` collisions).
- **Persist structured fields in compose.** Change `_to_claim_records` / `_render_to_dict` (`engine.py:215,605`) to persist per-module confidence, per-section summary, numeric claim values (cash/debt/runway as numbers, not prose), the peak-sales **base**, and the **rNPV** (`rnpv_claim` is currently never called). Without this the entire Companies section is parsing prose. Highest-leverage change in the project.

### Tier 1 — major (scope + build)
- **Figures are largely unbuilt in the memo package.** No figure extraction on ingest (ingestion is text-only), no figure→document link (`FigureRecord` has no `doc_id`; the manifest is hand-seeded synthetic PNGs), and the VLM+CV dual-read is hand-calibrated to **one** figure (`fig02_waterfall`) and does not generalize. Arbitrary figures get a single-read `HarvestedExtractor` with free-text labels, so "also appears in" won't join without semantic matching. **Decision required:** ship figures on precomputed/sample data, or build the real pipeline (fetch PDFs, adapt `corpus/pdf_figures.py`, add `doc_id`, generalize the CV read or accept VLM-only and soften the confidence story). Biggest scope fork.
- **No catalyst data.** No catalyst module, no date fields; regulatory is told to say "not disclosed". Build an extractor or weaken the dashboard's "next catalyst" to a proxy (trial `primary_completion_date`), which is not a readout date.
- **Fair-value-vs-market needs four pieces, not one:** price (Databento, new), shares (EDGAR, not currently structured), market cap (missing by design), rNPV (computed, not persisted).
- **No global search.** No `/search` route; retrieval is per-company only; companies/trials/filings are three different accessors. Net-new unified layer for the top bar.
- **Generation has no guards.** `compose_memo` is callable but has **no per-company lock/idempotency** and **no rate-limit/retry/cost-cap** around 10+ serial paid model calls. A "Generate" click is unbounded cost and time. Add a job runner with a lock, status polling (the mock's progress state), retries, and a budget cap.
- **Activity run-diffs aren't computable.** Manifests are overwritten and write-vs-skip isn't recorded, so `docs_added`/`docs_updated` can't be produced. Change ingestion to emit and persist per-run diffs.

### Tier 2 — contained (specify)
- **Audit reverse-lookup:** add `LedgerStore.get_evidence_by_id` (evidence_id is the PK) + an evidence→claim→memo join, or keep `memo_id` in the audit route.
- **Config / auth / CORS:** all absent. `server.py` never loads `.env`; no CORS (a cross-origin Next frontend is blocked); no auth. All net-new.
- **Object storage:** annotated images write to a path that falls back to the system temp dir. Upload to object storage with stable URLs.

### Correction
`claude-opus-5` and the `output_config` / adaptive-thinking API surface are **real and current** in this environment; generation runs with real credentials. (An earlier note in this project called these placeholders. That was wrong.)

### Order
Tier 0 first (packaging, then the compose persistence-refactor), then the figure-scope decision, before the screens fan out. §9 reflects this.

---

## 2. Target architecture

```
┌───────────────────────┐   HTTP/JSON   ┌────────────────────────┐     ┌──────────────┐
│  Next.js (App Router) │ ────────────► │  FastAPI (backend/)    │ ──► │  Postgres    │
│  on VERCEL            │ ◄──────────── │  routers · pydantic    │ ◄── │  (managed)   │
│  design system        │               │  services · repos      │     │  system of   │
│  Auth.js / VC pw-prot │               │  on Railway/Render/Fly │     │  record      │
└───────────────────────┘               └───────┬────────┬───────┘     └──────────────┘
                                                 │        │ enqueues            ▲
                                                 │ import ▼                     │ repos
                                                 │   ┌─────────────────────┐    │
                                                 │   │  Worker (same host) │────┘
                                                 │   │  ingest · compose · │
                                                 │   │  figure extraction  │
                                                 │   └──────────┬──────────┘
                                    ┌────────────┴─────────────┐│ imports
                                    │  Compute libraries        ││
                                    │  memo/ (ingestion, rag,   ││   ┌──────────────────┐
                                    │   analysis, compose,      │┼──►│ Object storage    │
                                    │   report, figures, trace) ││   │ (Vercel Blob/S3)  │
                                    │  figure-extraction/       ││   │ images, raw docs  │
                                    │   (evaluation, corpus, cv)││   └──────────────────┘
                                    └───────────────────────────┘│
```

Principles:
- **Postgres is the single system of record** for everything the app serves (companies, documents, figures, extractions, memos, claims, evidence, runs, prices). The API reads and writes Postgres via repository classes; it does not read the packages' local file stores at request time.
- **The two Python packages are compute libraries, not stores.** All bespoke logic (analysis modules, RAG, the figure router, the `cv/` pixel measurement, the quantity-key contract, the confidence/interval and dual-read model) is preserved untouched. Only their *persistence* is redirected to Postgres + object storage.
- **Long jobs run on a worker, never inline.** Ingestion, memo generation, and figure extraction call Claude and take minutes. The API enqueues them; the worker runs the compute libraries and writes results to Postgres. This is why the Python side lives on an always-on host, not Vercel serverless.
- **Blobs** (figure images, raw filings) go to object storage; Postgres holds the URLs.

---

## 3. Repository rearchitecture

### 3.1 Monorepo layout

```
perceptive-ai-case-study/
├── frontend/                 # NEW — Next.js app
├── backend/                  # NEW — FastAPI app
│   ├── app/
│   │   ├── main.py           # FastAPI() + router mounts + CORS
│   │   ├── deps.py           # shared deps: data root, store singletons
│   │   ├── routers/          # one module per nav section
│   │   │   ├── companies.py
│   │   │   ├── data.py
│   │   │   ├── figures.py
│   │   │   └── activity.py
│   │   ├── models/           # pydantic response models (mirror dataclasses)
│   │   ├── services/         # orchestration over memo/ and evaluation/
│   │   ├── repos/            # Postgres repositories (§4.2)
│   │   └── jobs/             # worker entrypoints: ingest, compose, extract
│   ├── db/migrations/        # SQL migrations for the schema in §4.2
│   └── pyproject.toml        # depends on the two packages below
├── investment-memo/          # EXISTING — keep as installable package `memo`
├── figure-extraction/        # EXISTING — keep as installable package
├── data/                     # shared runtime data (single root)
└── docs/webapp-build-spec.md # this file
```

Install the two existing packages editable into the backend env (`pip install -e ../investment-memo -e ../figure-extraction`) so `from memo...` and `from evaluation...` / `from corpus...` resolve. Package the two roots properly (add `pyproject.toml`/`setup.cfg` to each) if not already importable.

### 3.2 What moves, what is retired, what is added

| Component | Action |
|---|---|
| `memo/` package | Mostly unchanged, BUT `compose/engine.py` + `ledger` take the pre-work persistence refactor (0b): persist per-module confidence, per-section summary, numeric claim values, rNPV, and peak base. |
| `figure-extraction/evaluation`, `figure-extraction/corpus` | Keep. Import `VlmExtractor`, `HarvestedExtractor`, `FigureStorage`. |
| `investment-memo/app/` (Flask) | **Retire.** Its routes and audit logic (`server.py`) are the reference for the FastAPI endpoints; templates are reference for the audit drawer markup. |
| `data/` | One shared root for backend. Same `ledger.db`, `memos/`, `<company>/<source>/`, `corpus/`, `traces/`. |
| Persistence | **New**: Postgres (§4.2) as system of record + object storage for blobs. Repositories replace the packages' file stores; a one-time loader migrates existing `data/` in. |

### 3.3 Persistence: Postgres + object storage (replaces the file stores)

Today Stage 2 writes `data/ledger.db`, `data/memos/*.json`, `data/<company>/<source>/*.json`, `data/traces/*.jsonl`; Stage 1 writes `data/corpus/<source>/<figure_id>/`. On Vercel/serverless there is no persistent local disk, and the two stores don't join, so we consolidate on Postgres.

Approach: introduce thin **repository** interfaces the packages write through, backed by Postgres, so the compute stays put and only the write target changes.
- `LedgerStore` gains (or is wrapped by) a Postgres backend. Its `MemoRecord`/`ClaimRecord`/`EvidenceRecord` map 1:1 to the `memos`/`claims`/`evidence` tables (§4.2), so this is mechanical.
- Ingestion `Storage.write_document` writes a `documents` row; `text`/`raw` go to a `text` column and object storage respectively; keep the content-hash dedup (`Document.hash`).
- corpus `FigureStorage.write_figure` writes a `figures` row + uploads `image_bytes` to object storage.
- A new `extractions` table persists `Prediction`s (none exist today, §7.6).
- `trace.py` keeps writing per-run JSONL for debugging; run summaries land in `ingestion_runs` / `generation_runs`.

**Migration:** the five companies' ingestion is already run, so write a one-time loader that walks the existing `data/` (ledger.db, `memos/`, `<co>/<src>/`, `corpus/`) and inserts into Postgres. After that, the CLI/worker write to Postgres directly.

### 3.4 Hosting and auth

- **Frontend:** Vercel (Next App Router). Server components fetch the FastAPI over HTTPS. Next does **not** query Postgres directly; all data access goes through FastAPI, so there is one Python boundary over the data model.
- **Backend + worker (chosen shape):** one small always-on host (**Render**) running **both** the FastAPI (serves reads) and the worker (runs the long Python jobs) over the same Postgres. This was chosen over (a) Next-reads-Postgres + a jobs-only worker and (b) all-Vercel with a durable workflow engine, to keep a single language and one clean API over the data. A separate host is required because the engine jobs (ingest, compose, figure extraction) call Claude for minutes and cannot run in Vercel's time-limited, stateless functions. The worker consumes a job queue (`BackgroundTasks` for a single instance, or Redis/RQ for retries and concurrency).
- **Postgres:** managed (Neon, Supabase, or Vercel Postgres). **Object storage:** Vercel Blob or S3.
- **Price feed:** a Databento client on the worker refreshes `price_snapshots` on a schedule (§7.4).
- **Auth:** start with Vercel password protection (one shared gate, minutes to set up). Move to Auth.js (NextAuth) with Google Workspace SSO for named analysts; Next middleware guards routes and forwards a signed session token that FastAPI verifies. Avoid raw HTTP basic auth; Auth.js is the same effort and gives real users.

---

## 4. Canonical data model

The app needs a handful of entities. Most map directly to existing dataclasses; the gaps are called out and specified in §7.

| Entity | Backed by (exists) | Gap |
|---|---|---|
| **Company** | none — companies are bare strings | NEW registry (`registry.json`): ticker, name, lead asset, indication, phase |
| **CompanySummary** (dashboard row) | derived from `valuation`, `peak_sales`, analysis confidences, trials | NEW aggregation service |
| **Memo** | `MemoRecord` + rendered `data/memos/<id>.json` | exists |
| **Claim** | `ClaimRecord` | exists |
| **Evidence** | `EvidenceRecord` | exists |
| **Document** | `Document` + `data/<co>/<src>/<doc_id>.json` | list/index across sources is NEW |
| **StructuredRecord** (trial/filing/price) | `TrialRecord`, `FilingRecord`, `PriceRecord` | exists |
| **Figure** | `figures/FigureRecord` (memo) + `corpus/FigureRecord` (Stage 1) | NEW unified Figure with parent `doc_id` + extracted values |
| **Extraction** (figure read) | `evaluation/Prediction` | NOT persisted today — NEW predictions store |
| **IngestionRun** | `IngestManifest` per source | NEW unified runs store |
| **GenerationRun** | `MemoRecord.status` + `data/traces/<id>.jsonl` | NEW runs store (join) |

### 4.1 Verbatim field references (source of truth for response models)

- `MemoRecord`: `memo_id, company, status ("draft"|"complete"|"refused"), recommendation?, thesis?, notes, created_at` (`memo/ledger/schema.py:53`)
- `ClaimRecord`: `memo_id, section, module, statement, confidence:float, rationale, value?, claim_id, evidence:[EvidenceRecord]` (`schema.py:38`)
- `EvidenceRecord`: `doc_id, source, doc_type, url, quote, date?, chunk_id?, evidence_id` (`schema.py:24`)
- Rendered memo JSON: `{memo_id, company, title, recommendation, thesis, status, sections:[{section_id, title, prose, citations:[{marker, evidence_id, url, label}], figures:[{figure_id, caption, image_ref, claim_id}]}]}` (`memo/compose/engine.py:605` `_render_to_dict`)
- `MEMO_SECTIONS` order/ids: `thesis, overview, moa, pos, regulatory, peak_sales, valuation(module="price"), risks, catalysts, sources` (`memo/report/structure.py:23`)
- `Document`: `company, source, doc_type, doc_id, title, url, published?, retrieved_at, metadata, text, raw` (`memo/ingestion/base.py:74`); on disk `data/<company>/<source>/<doc_id>.{json,txt,raw}`
- `TrialRecord`: `nct_id, title, url, phases, status?, conditions, interventions, enrollment?, lead_sponsor?, start_date?, primary_completion_date?, primary_outcomes` (`memo/rag/structured.py:17`)
- `FilingRecord`: `accession, form, url, filing_date?, report_date?, primary_document?` (`structured.py:35`)
- `PriceRecord`: `drug, generic?, manufacturer?, price_per_unit, unit, period?, source, doc_type, doc_id, url` (`structured.py:47`)
- `compute_rnpv(...) -> {rnpv, unadjusted_npv, pos, revenue_curve, cash_flow_curve, assumptions, rnpv_str, note}` (`memo/analysis/valuation.py:40`)
- `estimate_peak_sales(params, sensitivity_margin=0.2) -> {treatable_population, patients_on_drug, gross_revenue, risk_adjusted, sensitivity:{low,base,high}, range_str, sensitivity_margin}` (`memo/analysis/peak_sales.py:109`); params: `epidemiology_population, addressable_fraction, peak_penetration, annual_net_price, probability_of_success`
- `Prediction` (figure read): `figure_id, figure_type, quantity_key, value_raw, unit?, interval_low?, interval_high?, confidence?, method, reads:{sample_0,sample_1}` (`figure-extraction/evaluation/types.py:80`)
- corpus `FigureRecord`: `source, figure_id, figure_type, title, caption, context, url, image_url, image_ext, license?, published?, retrieved_at, ground_truth:[GroundTruthValue], metadata, ...` on disk `data/corpus/<source>/<figure_id>/` (`figure-extraction/corpus/base.py:129`)
- `RunSession.cost(model)` and `.tokens`, `_PRICING` per-model (`memo/trace.py:136,116`) — used for internal run records, NOT surfaced in UI

### 4.2 Postgres schema

The system of record. Column names track the dataclass fields in §4.1 so the repositories are mechanical. Blob columns (`image_ref`, `raw_ref`) hold object-storage URLs. The figure↔document containment link and cited-by are plain foreign keys.

```sql
create table companies (
  ticker text primary key,
  name text not null, lead_asset text, indication text, phase text,
  shares_outstanding bigint,         -- from EDGAR XBRL; market_cap = price × shares (§7.4)
  shares_as_of date                  -- freshness of the share count (filing date)
);

create table documents (
  doc_id text primary key,
  company text references companies(ticker),
  source text, doc_type text, title text, url text,
  published date, retrieved_at timestamptz, hash text,   -- content hash for dedup
  metadata jsonb, text text, raw_ref text                -- raw_ref -> object storage
);

create table figures (
  figure_id text primary key,
  doc_id text references documents(doc_id),   -- ◄── the containment link
  company text references companies(ticker),
  figure_type text, caption text,
  image_ref text,                              -- object-storage URL
  region jsonb                                 -- {kind, coords, text?} for the overlay
);

create table extractions (                      -- persisted Prediction (§7.6)
  id bigserial primary key,
  figure_id text references figures(figure_id),
  quantity_key text, label text,
  value_raw text, unit text,
  interval_low double precision, interval_high double precision,
  confidence double precision, method text,
  reads jsonb, agreement double precision,
  tier text                                     -- 'unattended'|'review'|'cannot_resolve'
);

create table memos (
  memo_id text primary key,
  company text references companies(ticker),
  status text, recommendation text, thesis text, notes text, created_at timestamptz
);

create table claims (
  claim_id text primary key,
  memo_id text references memos(memo_id),
  section text, module text, statement text,
  confidence double precision, rationale text,
  value text, value_num double precision, value_unit text   -- 0b: parsed numeric beside prose
);

-- 0b: per-section rollup the modules compute today but compose currently DROPS
create table memo_sections (
  memo_id text references memos(memo_id),
  section_id text, module text,
  confidence double precision,       -- per-module confidence → conviction dots (§7.2)
  tier text,                         -- high|med|low
  summary text,                      -- one-line metric-panel takeaway
  primary key (memo_id, section_id)
);

create table evidence (
  evidence_id text primary key,
  claim_id text references claims(claim_id),
  doc_id text references documents(doc_id),     -- cited-by for documents
  figure_id text references figures(figure_id), -- nullable: cited-by for figures
  source text, doc_type text, url text, quote text, date date, chunk_id text
);

-- structured records mirror TrialRecord / FilingRecord / PriceRecord (§4.1)
create table trials  ( nct_id text primary key, company text, title text, url text,
  phases jsonb, status text, conditions jsonb, interventions jsonb, enrollment int,
  lead_sponsor text, start_date date, primary_completion_date date, primary_outcomes jsonb );
create table filings ( accession text primary key, company text, form text, url text,
  filing_date date, report_date date, primary_document text );
create table drug_prices ( id bigserial primary key, company text, drug text, generic text,
  manufacturer text, price_per_unit double precision, unit text, period text,
  source text, doc_type text, doc_id text, url text );

-- market data (§7.4): price from Databento; market_cap = price × EDGAR shares
create table price_snapshots (
  ticker text references companies(ticker),
  market_price double precision, market_cap double precision, shares bigint,
  as_of timestamptz, primary key (ticker, as_of)
);

-- Activity (§7.5)
create table ingestion_runs (
  run_id text primary key, ran_at timestamptz, scope text, sources jsonb,
  docs_added int, docs_updated int, duration_s double precision,
  status text, note text                        -- 'success'|'partial'|'failed'
);
create table generation_runs (
  run_id text primary key,                      -- = memo_id
  company text, ran_at timestamptz, trigger text,
  status text, output text, refusal_reason text, -- status 'completed'|'refused'|'running'
  model text, input_tokens int, output_tokens int,
  cost_usd double precision, duration_s double precision, memo_id text
);  -- written by compose_memo itself; cost/tokens surfaced on Activity for now (§7.5, §11)

create index on figures(doc_id);
create index on extractions(figure_id);
create index on claims(memo_id);
create index on evidence(claim_id);
create index on evidence(doc_id);
create index on evidence(figure_id);
```

Queries the screens reduce to:
- Figure counts per document: `count(figures) group by doc_id`.
- Cited-by (document): `count(distinct claim) from evidence where doc_id = ?`.
- Cited-by (figure): `evidence where figure_id = ?`.
- "Also appears in / conflict": self-join `extractions` on `quantity_key` across different `figure_id`/`doc_id`, diff `value_raw` beyond tolerance.
- Coverage row: `memos` (latest per company) + `extractions`/analysis for conviction + `price_snapshots` + nearest `trials.primary_completion_date`.

---

## 5. API contract

All endpoints are read-only GET unless noted. Base path `/api`. Response models are Pydantic mirrors of the dataclasses above; field names below are the JSON keys.

### 5.1 Companies

**`GET /api/companies`** → coverage dashboard rows.
```
[{
  ticker, name, lead_asset, indication, phase,
  thesis: "long"|"short"|"neutral",
  conviction: { efficacy, approval, regulatory, market },   // each "high"|"med"|"low"
  fair_value_per_sh: number,        // from compute_rnpv → per-share (needs share count)
  market_price: number|null,        // GAP: price feed (§7.4)
  upside_pct: number|null,
  peak_sales_base: string,          // estimate_peak_sales.range_str / base
  next_catalyst: { label, date }|null,   // from trials/filings
  updated_at: string
}]
```
Aggregation service required (§7.1). `conviction` maps per-section analysis confidence to a tier (§7.2).

**`GET /api/companies/{ticker}`** → company detail header + panels.
```
{
  ticker, name, lead_asset, indication, phase, thesis,
  kpis: { fair_value, market_price, upside, market_cap, cash, runway },
  metrics: {                        // the four questions
    efficacy:   { confidence: "high"|..., takeaway },
    approval:   { confidence, takeaway },
    regulatory: { confidence, takeaway },
    market:     { confidence, takeaway }
  },
  memo_id: string|null
}
```
`takeaway` = the persisted per-section summary (`memo_sections.summary`, added in 0b). `cash`/`debt`/`runway` come from the `price` module's claims parsed to `value_num` in 0b — **`FilingRecord` carries no financials**, these are prose in `claim.statement` today. `market_cap` = latest `price_snapshots` (price × shares); `upside` = rNPV vs market cap.

**`GET /api/memos/{memo_id}`** → the rendered memo, verbatim from `compose.engine.load_artifact(memo_id)`. Shape already defined in §4.1. This backs the memo prose, inline citations, and inline figures.

**`GET /api/companies/{ticker}/memo`** → convenience: latest memo for the company (`LedgerStore.latest_memo`) then its rendered artifact.

**`GET /api/evidence/{evidence_id}`** → audit drawer target.
```
{ evidence: EvidenceRecord, claim: ClaimRecord }
```
Needs a new `get_evidence_by_id(evidence_id)` (evidence_id is the PK) joining evidence→claim→memo. The current Flask route only resolves within a known `memo_id` (`app/server.py:102`), which this memo-less route cannot do; the reverse lookup does not exist today. O(1) once the store method + join are added.

Actions (POST):
- **`POST /api/companies/{ticker}/generate`** → enqueue a memo generation (wraps `compose_memo`), returns a `GenerationRun` id. Async job (§7.5).

### 5.2 Data (documents + figures)

**`GET /api/documents?company=&type=&date_from=&date_to=&q=`** → the Data library list.
```
[{
  doc_id, title, source, doc_type, company, published,
  figure_count: number,       // GAP: join to figures (§7.3)
  cited_by_count: number       // GAP: count evidence rows referencing this doc_id
}]
```
Index across all `data/<company>/<source>/` via `Storage.load_documents` per (company, source), unioned. For performance, build a lightweight index (§7.3).

**`GET /api/documents/{doc_id}`** → document view.
```
{
  document: Document,                 // incl. text for rendering
  figures: [FigureSummary],           // figures in this document (§5.3)
  cited_by: [{ memo_id, company, section, claim_count }]
}
```

**`GET /api/figures?company=&figure_type=`** → the Figures toggle (flattened across documents).
```
[FigureSummary]
```

### 5.3 Figures

`FigureSummary`:
```
{ figure_id, name, figure_type, doc_id, company,
  headline: { quantity_key, value, confidence: "high"|... },   // top extracted value
  tier: "unattended"|"review"|"cannot_resolve",
  thumb_ref }
```

**`GET /api/figures/{figure_id}`** → figure detail view.
```
{
  figure_id, name, figure_type,
  image_ref, region,                  // for the annotated overlay (corpus region / manifest region)
  parent: { doc_id, title, source, page },
  extractions: [{                     // from persisted Prediction (§7.6)
    quantity_key, label, value_raw, unit,
    interval_low, interval_high, confidence, method,
    reads: { sample_0, sample_1 }, agreement, tier
  }],
  also_appears_in: [{ doc_id, title, value_raw, delta }],   // GAP: cross-doc match (§7.3)
  cited_by: [{ memo_id, section, claim_id }]
}
```

Action:
- **`POST /api/figures/{figure_id}/extract`** → run `VlmExtractor.extract` (or `HarvestedExtractor` for open-schema figures) and persist the `Prediction`s (§7.6). Async.

### 5.4 Activity

**`GET /api/activity/ingestion-runs?limit=`** → from the runs store (§7.5), which unifies per-source `IngestManifest`s.
```
[{ run_id, when, scope: "all"|ticker, sources:[...], docs_added, docs_updated,
   duration_s, status: "success"|"partial"|"failed", note }]
```

**`GET /api/activity/generations?limit=`** → from the `generation_runs` table (§7.5).
```
[{ run_id: memo_id, when, company, trigger: "scheduled"|"manual"|"data_change",
   status: "completed"|"refused", output: "N claims · M sections" | refusal_reason,
   model, input_tokens, output_tokens, cost_usd, duration_s,   // surfaced for now (§11)
   memo_id }]
```
`status`/`output` from `MemoRecord.status` and `.notes` (refusal string: `"refused: required section(s) had no grounded claims: ..."`, `engine.py:140`).

**`GET /api/activity/health`** → the health strip tiles: last successful ingest, next scheduled run, memos in 24h (completed/refused), needs-attention count.

Actions:
- **`POST /api/activity/ingestion-runs`** → trigger a run (wraps `cli ingest`). Async.
- Generation trigger is `POST /api/companies/{ticker}/generate` (§5.1).

---

## 6. Screen → route → data → module → gap

| Screen | Frontend route | Endpoint(s) | Backing module | Gap |
|---|---|---|---|---|
| Coverage dashboard | `/` | `GET /companies` | `valuation`, `peak_sales`, analysis confidences, `structured.trials` | aggregation service (§7.1), conviction tiering (§7.2), price feed (§7.4) |
| Company detail | `/company/[ticker]` | `GET /companies/{t}`, `GET /companies/{t}/memo` | `ledger`, `compose.load_artifact`, `report` | exists; KPIs need cash/shares extraction |
| Audit drawer | overlay on company page | `GET /evidence/{id}` | `ledger` | needs `get_evidence_by_id` (no reverse index today) |
| Data library | `/data` | `GET /documents`, `GET /figures` | `ingestion.Storage`, figures | document index (§7.3), figure counts, cited-by counts |
| Document view | `/data/document/[docId]` | `GET /documents/{id}` | `ingestion.Storage`, figures, `ledger` | figure↔doc link (§7.3), inline extraction affordance |
| Figure view | `/data/figure/[figureId]` | `GET /figures/{id}` | `evaluation.VlmExtractor`, `corpus.FigureStorage` | predictions persistence (§7.6), cross-doc conflict (§7.3) |
| Activity | `/activity` | `GET /activity/*` | manifests, `trace`, `ledger` | runs store (§7.5) |

---

## 7. What must be built (backend gaps, prioritized)

These are the only genuinely new pieces of backend logic. Everything else is serialization over existing code.

### 7.1 Company registry + summary aggregation
- `registry.json`: the five tickers with `{ticker, name, lead_asset, indication, phase, shares_outstanding?}`. Small, hand-maintained. This is the "coverage universe" (extensible later).
- A `CompanySummaryService` that, per ticker, assembles the dashboard row from: latest memo (thesis/recommendation), per-section confidences (§7.2), `compute_rnpv` for fair value, `estimate_peak_sales` for peak, and the nearest catalyst from `StructuredStore.trials()`/`filings()`. Cache per run; recompute on new generation.
- Fair value per share needs `shares_outstanding` (from `FilingRecord` XBRL or the registry) to divide rNPV.

### 7.2 Conviction tiering
- The four dots = per-question confidence mapped to `high|med|low`. Source: the analysis modules' confidence for moa/pos/regulatory/peak_sales. Today claims carry `confidence:float`; add a per-section rollup (mean grounded-claim confidence, or the section lead claim). Define thresholds (e.g. ≥0.66 high, ≥0.4 med, else low). Keep the thresholds in one place.

### 7.3 Document index + figure↔document linkage (now Postgres FKs)
With Postgres as the store, most of this stops being bespoke code and becomes queries against §4.2:
- **Index / list:** `GET /documents` is a filtered `select` over `documents`. No file walking.
- **Figure link:** `figures.doc_id` is the containment link. Populate it at ingest: corpus figures inherit the source document; when the ingester detects figures in a document, it inserts `figures` rows with that `doc_id`.
- **cited_by / figure counts:** the index queries listed under §4.2 (counts over `figures` and `evidence`). This requires evidence that rests on a figure to set `evidence.figure_id` (extend the grounding step so a figure-backed claim records the figure id alongside the doc id).
- **also_appears_in / conflict:** self-join `extractions` on `quantity_key` across figures; flag when `value_raw` differs beyond the quantity's tolerance. This surfaces the Stage-1 "figures disagree" case.

### 7.4 Market data adapter — Alpha Vantage
- A `MarketDataProvider.get(ticker) -> {market_price, market_cap, shares?, as_of}` on the worker refreshes `price_snapshots`, backed by Alpha Vantage (free key in `.env` as `ALPHAVANTAGE_API_KEY`).
- **Endpoints:** `GLOBAL_QUOTE` → latest price; `OVERVIEW` → `MarketCapitalization` + `SharesOutstanding` (and name/description, usable to seed the registry). Market cap comes **directly**, so `upside = rnpv_total / market_cap - 1` and `fair_value_per_share = market_price * (rnpv_total / market_cap)` — no separate share count needed for the math (shares stored for display only).
- **Rate limit:** free tier ~25 requests/day. Cache aggressively: fetch `OVERVIEW` once/day (slow-moving), `GLOBAL_QUOTE` a couple times/day. Five names × 2 endpoints = ~10/day for a daily refresh, within budget.
- **Consensus:** no feed in this configuration; the memo reconstructs an implied expectation and states its assumptions (§11).
- Keep the interface abstract so a stub can stand in for local dev, and so Databento/FactSet/Polygon can replace Alpha Vantage later without touching anything downstream.

### 7.5 Runs store (Activity) — `ingestion_runs` / `generation_runs`
- **IngestionRun** row written when the worker runs `cli ingest`: aggregate that run's per-source `IngestManifest`s into scope, sources, docs added/updated (diff vs prior), duration, status (`success` if no `manifest.errors`, `partial` if some, `failed` if all), note (first error).
- **GenerationRun** row: `compose_memo` writes it itself now (so the CLI and the worker both get it), with `run_id = memo_id`, status/output from `MemoRecord.status` + `.notes`, plus model, tokens, `cost_usd`, and duration from the `RunSession`. Cost + tokens are **surfaced on the Activity page for now** as a dev aid (§11), to become internal-only later.
- Each triggered job inserts a row with `status = running`, then updates it on completion. Use the worker's queue, not an inline request.

### 7.6 Predictions store (Stage 1) — `extractions` table
- Today `Prediction`s are in-memory only. Persist them to `extractions` (§4.2), one row per `(figure_id, quantity_key)`, plus computed `agreement` and `tier`.
- Populate on the worker: `HarvestedExtractor` for arbitrary real figures (open-schema, `method="vlm_harvested"`, **single read, no CV cross-check, `reads={}`**). The `VlmExtractor` + `cv/` dual-read only covers the closed set, and CV is presently calibrated to **one** figure (`fig02_waterfall`) — a real ingested figure gets a VLM-only read unless per-figure CV calibration (or a general CV method) is built. `figure_id` joins to the `figures` row. Run as part of ingest and via `POST /figures/{id}/extract`.
- `tier`: derived from confidence + dual-read agreement (agree + high → `unattended`; diverge or low → `review`; interpretive/unresolvable → `cannot_resolve`). Analyst-facing triage only; the eval metrics behind it stay out of the API.
- The bespoke figure logic (router, `cv/` measurement, quantity-key contract, confidence/interval model) is unchanged; only the write to `extractions` is new.

---

## 8. Frontend structure (Next.js)

### 8.1 Routes (App Router)
```
app/
  layout.tsx                 # AppShell: Sidebar + TopBar
  page.tsx                   # Coverage dashboard  → GET /companies
  company/[ticker]/
    page.tsx                 # Company detail      → GET /companies/{t} + /memo
    @drawer/[evidenceId]/    # Audit drawer (parallel route) → GET /evidence/{id}
  data/
    page.tsx                 # Library (Documents/Figures toggle) → GET /documents | /figures
    document/[docId]/page.tsx        # → GET /documents/{id}
    figure/[figureId]/page.tsx       # → GET /figures/{id}
  activity/page.tsx          # → GET /activity/*
```
Server components fetch from FastAPI directly (server-side). The audit drawer is a parallel/intercepting route so a claim opens the drawer without a full navigation.

### 8.2 Component inventory (maps to the Figma components)
- `AppShell`, `Sidebar` (expandable sections: Companies + tickers, Data, Activity), `TopBar` (title, search, sync chip)
- `StatTile` (summary/health grid), `MetricPanel` (the four questions)
- `CoverageTable` + `HeaderDropdown` (per-column sort/filter), `DataTable` (generic)
- `ConfidenceChip`, `ConvictionDots`, `ThesisChip`, `StatusChip`, `TypeChip`
- `ClaimBlock` (memo claim, selected state), `AuditDrawer`
- `FigureChart` (see §8.3), `FigurePanel` (extracted value + method + tier), `FigureListItem`
- `SegmentedToggle`, `Breadcrumb`

Use Figma Code Connect to map each Figma component to its React counterpart so design-to-code reuses instead of duplicating.

### 8.3 Rendering figures
Two options per figure:
- **Source image + overlay:** show `image_ref` and draw the annotation from `region` coords (arrow/box). Truest to the real slide. Preferred for real corpus figures.
- **Re-rendered chart:** an SVG chart component built from extracted values (what the mock does). Good for demo polish; only viable where the underlying series is available. Default to source-image-plus-overlay for real data.

### 8.4 Styling
Tailwind, configured from the tokens in Appendix A. Two fonts: Inter (sans) and JetBrains Mono (all numerals, tickers, trial ids, dates). Dark sidebar, light data-dense content. Theme-aware optional (mock is light-first).

---

## 9. Build sequence

**Pre-work first** (see "Gaps and required pre-work") — the screens depend on it, so do it before any parallel fan-out:

- **0a. Package both trees** and install as deps into the backend env (resolves the import blocker).
- **0b. Compose persistence-refactor:** persist per-module confidence, per-section summary, numeric claim values, peak base, and rNPV, so the app reads structured data, not prose. This unblocks the entire Companies section at once.
- **0c. Decide figure scope:** precomputed/sample data vs the real extract-on-ingest pipeline (§7.3, §7.6).

Then:

1. **Scaffold:** monorepo, FastAPI + CORS + auth gate, worker, Next app, Tailwind from tokens. Stand up Postgres (§4.2); run the migration loader to import the existing `data/`.
2. **Design system:** tokens → tailwind config, then the shared components (chips, tables, shell) against static data.
3. **Company detail + audit** (add `get_evidence_by_id`; reads ledger + rendered memo). Lowest-gap, proves the audit primitive end to end.
4. **Coverage dashboard:** company registry + summary aggregation (§7.1, §7.2, needs 0b) + price adapter (§7.4).
5. **Data library + document view:** document index + figure link (§7.3).
6. **Figure view:** per the scope decision in 0c (predictions store + Stage-1 wiring, §7.6).
7. **Activity:** runs store (needs the ingestion run-diff change, §7.5) + triggers with the job guards.
8. **Actions:** generate/re-run as **guarded** background jobs (lock, status, retries, budget cap).

Ship 0a–3 as the first working slice; the rest layer on.

---

## 10. Decisions and remaining inputs

Resolved:
- **Price:** Databento (§7.4). **Deploy:** Next on Vercel, FastAPI + worker on a separate host, managed Postgres + object storage (§3.4). **Auth:** Vercel password protection now, Auth.js + Google SSO later. **Data:** ingestion already run for the five companies, migrate it in (§3.3). **Generation:** on-demand from the app (§5.1). **Stage 1:** wire the live extractor, extract-on-ingest, persist to `extractions` (§7.6). **Brand:** Perceptive Research OS.

Also resolved: **Backend host** = Render (FastAPI + worker + Postgres). **`shares_outstanding`** = EDGAR XBRL (`dei:EntityCommonStockSharesOutstanding` via SEC `companyfacts`), already ingested.

Still to confirm:
1. **Databento specifics:** which equities dataset/schema and the API key. (FactSet deferred for now; it can replace price + shares + consensus later behind the `MarketDataProvider` interface.)
2. **When to switch auth** from the shared password to per-user SSO (before or after first internal users).

---

## 11. Non-goals / stubs / open questions

- **No eval surfaces in the app.** The eval harness (`memo/eval/`, figure-extraction reports) stays internal. The only machine-confidence in the UI is the tier + figure interval + dual-read agreement.
- **Cost/tokens/model: surfaced on Activity for now, internal later.** Persisted per run in `generation_runs` and shown on the Activity page as a development aid; the plan is to hide them once the app is in analysts' hands, leaving only status/outcome.
- **Consensus estimates** have no clean feed in the Databento + EDGAR configuration; the memo reconstructs an implied expectation from public data and states its assumptions. A vendor (FactSet) would replace this later behind the same interface.
- **Multi-asset companies:** memo is per company for v1 (per the design). Revisit if a company needs per-asset memos.
- **Editing:** read-and-audit for v1. Value cells, the peak-sales assumptions block, and a notes rail are designed to become editable later without a redesign (analyst overrides an assumption, valuation recomputes). Out of scope now.

---

## Appendix A — Design tokens (Tailwind)

From the Figma Foundations page. Drop into `tailwind.config.ts` `theme.extend`.

```ts
export const tokens = {
  colors: {
    canvas:      "#F5F6F8",
    surface:     "#FFFFFF",
    sunken:      "#EEF1F5",
    sidebar:     "#0E1621",
    sidebarHover:"#1B2735",
    border:      "#E4E8ED",
    borderStrong:"#CFD6DF",
    text:        "#111823",
    textSecondary:"#586173",
    textTertiary:"#8B95A5",
    textInverse: "#FFFFFF",
    textSidebar: "#AEB9C7",
    accent:      "#2563EB",
    accentText:  "#1D4ED8",
    accentSoft:  "#E7F0FE",
    accentSoft2: "#EEF3FF",
    confHigh:    "#15803D", confHighSoft: "#DCFCE7",
    confMed:     "#B45309", confMedSoft:  "#FBEBCF",
    confLow:     "#64748B", confLowSoft:  "#EAEEF3",
    long:        "#15803D", longSoft:     "#DCFCE7",
    short:       "#DC2626", shortSoft:    "#FCE4E4",
  },
  radius: { sm: "6px", md: "8px", lg: "12px", pill: "999px" },
  space:  { 1: "4px", 2: "8px", 3: "12px", 4: "16px", 5: "20px", 6: "24px", 8: "32px" },
  fontFamily: {
    sans: ['Inter', 'system-ui', 'sans-serif'],
    mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],   // all numerals, tickers, dates
  },
  fontSize: {
    display: ["28px", { fontWeight: 600 }],
    h1: ["22px", { fontWeight: 600 }],
    h2: ["17px", { fontWeight: 600 }],
    h3: ["14px", { fontWeight: 600 }],
    body: ["14px", { fontWeight: 400 }],
    bodySm: ["13px", { fontWeight: 400 }],
    caption: ["12px", { fontWeight: 500 }],
    micro: ["10px", { fontWeight: 600 }],
  },
};
```

Confidence semantics: high = green, medium = amber, low/cannot-resolve = slate. Thesis direction: long = green, short = red, neutral = slate. Tier chips reuse the confidence colors.
