# Follow-ups and deferred work

Running list of things we have consciously deferred, with enough context to pick each
up later. Ordered loosely by area. "Deferred" means we decided not to build it now, not
that it is unimportant.

## Ingestion and storage

- **Document versioning / point-in-time reconstruction (deferred).** Today one `doc_id`
  maps to one file, and re-fetching a changed document (a trial status flips, a filing is
  amended and restated) overwrites it. There is no way to reconstruct "what the corpus
  said as of date X." Doing this properly means immutable dated snapshots
  (`data/<company>/<source>/<timestamp>/...` with a `latest` pointer) or a version chain
  per doc_id. We are deliberately not building it now. We keep the door open for free by
  stamping every document with its date, so the corpus is already multi-temporal; only the
  *history of a single changing document* is unhandled. Revisit if the memo needs "as of"
  claims or amendment tracking (10-K/A, restatements).
- **EDGAR history beyond `filings.recent` (deferred).** The ingester only reads the recent
  filings block, not the older filings paged into `filings.files[*]` history JSONs. This
  caps how far back the corpus reaches. Needed if a memo wants multi-year trend analysis.
- **EDGAR XBRL numeric facts (not built).** Cash, shares outstanding, debt, and other
  tagged financials are available via the companyfacts API and should be pulled as
  structured fields (the citable numeric backbone for the price-vs-thesis section) rather
  than scraped from filing prose.
- **IR deck scraping + figures (not built).** The figures the memo must insert and annotate
  live in company IR/data decks. This is the fiddly second ingestion slice (per-site
  scraping, PDF/image extraction) and is the highest-risk source. Consider a hand-seeded
  document manifest per company as a fallback if a site blocks scraping.
- **ClinicalTrials sponsor precision.** `query.spons` matches lead sponsor and
  collaborators, so results can include trials where the company is only a collaborator.
  `lead_sponsor` is recorded per trial for downstream filtering; wire that filter when it
  matters.
- **EDGAR text quality.** Inline-XBRL filings begin with a block of machine tags; the
  chunker trims a leading run of it, but HTML-to-text does no table layout. Good enough to
  index, not a faithful render. Revisit if table figures need to be read from filing text.
- **Optional `--prune` on ingest.** With manifest-defined reads (below), orphan files are
  ignored but not deleted. A `--prune` flag could delete files not in the latest run for a
  tidy corpus. Low priority.

## Retrieval / RAG

- **Dense embeddings (deferred, evidence-gated).** v1 is BM25 + structured lookup. The
  first retrieval eval scored hit@8 = 1.00 on KYMR after fixes, so there is no evidence yet
  that we need semantic retrieval. Add it only when an eval shows recall gaps from
  vocabulary mismatch. The `Embedder` interface is in place; the open decision (OpenAI vs
  local vs Voyage) waits until then. Note the brief guarantees OpenAI keys and penalizes
  new accounts.
- **Reranker (interface stub only).** A cross-encoder rerank over fused candidates is left
  as a stub. Add if precision needs it once multi-relevant gold cases exist.
- **Structure-aware chunking upgrade.** Tables and figures are not yet preserved as typed,
  separately citable units. Needed for figure insertion and table-cell citations.

## Analysis modules

- **Built**: MoA (`MechanismModule`), calling Claude (`claude-opus-5`) with structured
  outputs. Grounding design: the model may cite only the labeled evidence we retrieved,
  and cited labels are resolved back to real chunks, so a citation can't be fabricated.
- **Live path unverified**: no `ANTHROPIC_API_KEY` or `ant` profile was present in the
  build environment, so the real-model run (`python -m memo.cli analyze --company KYMR`)
  has not been executed. Deterministic plumbing is unit-tested; the end-to-end model call
  needs a credentialed run. The installed SDK (0.86.0) does support the API shape used.
- **Not built**: PoS (evidence rubric, not generic phase-transition rates), regulatory
  path, peak sales (deterministic code model), and price-vs-thesis.

## Evals

- Built: retrieval recall (deterministic gold set, KYMR).
- Not built: faithfulness (claim entailed by cited evidence; LLM judge calibrated to human
  labels), numeric integrity (unit-tested peak-sales model), analytical quality (rubric +
  pairwise vs baseline), calibration (confidence vs correctness), refusal / red-team, and a
  versioned regression harness in CI with per-company dashboards.
- Gold sets: only KYMR retrieval exists. Add the other four companies and multi-relevant
  cases (so precision@k becomes meaningful).
- **All model-backed evals use real models, never mocks.** (Standing rule.)

## Product / UI (not built)

- The web app: one read/audit/compare/export screen over the claims + evidence the engine
  produces. Deliberately simple.
- Memo assembly: section planning, per-claim retrieval, drafting with inline citations, an
  adversarial verifier, and rendering the memo from stored claims.

## Cross-cutting

- **Refusal triggers.** Specify and implement when the system declines: insufficient public
  evidence for a required section, unresolved conflicting disclosure, staleness past a
  threshold.
- **Cost/model routing.** Measure and report cost per full memo run (the brief asks for it).
- **Multi-company demo.** The demo must work for all five (ABVX, KYMR, PRAX, IMVT, COGT).
  Only KYMR has been exercised end to end so far.
- **[FIXED] CLI does not pass per-source query terms.** `ingest` forwarded only `--limit`,
  so the comparator/pricing sources (openfda, cms, nadac, pubchem, pubmed) — which need
  `terms`/`drugs`/`compounds`/`term` — returned empty under `--source all`. Fixed by
  `memo/companies.py`: a hand-seeded profile per ticker (lead asset, asset codes,
  indications, oral comparators, literature terms) that `source_options()` maps to each
  source's option names, merged in by the CLI with explicit options still winning.
  `--no-profile` restores the old behaviour. Infused comparators are still owned by
  `memo.ingestion.asp.COMPARATORS` and read through the profile, so there is one table,
  not two. The profiles are analyst input and should be reviewed as such — the follow-up
  now is keeping them current, not wiring them.

## RAG consumability audit (2026-09-07)

Two read-only audits of the extracted data against the RAG layer. Fixed items are marked;
the rest are recommendation-only and deferred.

### Metadata / structured-field audit

- **[FIXED] P0 — bare-year dates broke date filtering.** `pubmed`, `cms`, `cdc` (and
  `preprints` on fallback) set `published` to a bare year; `"2024" < "2024-01-01"` sorts
  wrong, wrongly excluding docs from a `since` window in their own year. Fixed centrally:
  `Document.__post_init__` now runs `normalize_date()` (pads `YYYY`/`YYYY-MM`, collapses a
  `YYYY-YYYY` range to its start), so every source and every doc reloaded from disk is
  consistent. Regression tests added.
- **[FIXED] P1 — CMS source key.** Renamed `cms_spending` → `cms` for consistency with the
  other bare source keys (old on-disk `data/<co>/cms_spending/` is now orphaned; harmless,
  gitignored).
- **[FIXED] P1 — EDGAR metadata camelCase.** `filingDate`/`reportDate`/`primaryDocument`…
  renamed to snake_case; `StructuredStore._to_filing` updated to match.
- **[FIXED] P1 — drug-maker vocabulary.** Added a shared `manufacturer` (and `brand_name`
  on drugsfda) across openFDA label + Drugs@FDA; CMS already conformed. NADAC left as-is
  (its `ndc_description` doesn't cleanly split into brand/generic).
- **[deferred] P0 — PubChem docs are undated** (`published=None`, always pass date filters).
  Treated as intentional (chemistry is timeless); document the intent or set a deposit date.
- **[deferred] P2 — EDGAR `doc_type` exact-match.** `StructuredStore.filings(forms=["10-K"])`
  misses `10-K/A`; store a normalized base form and match on it.
- **[deferred] P2 — CMS double-ingest.** Passing both brand and generic can create two docs
  for one row; key `doc_id` off a stable identity.
- **[deferred] P2 — openFDA drugsfda date** is None unless an ORIG/AP submission exists;
  fall back to another submission date.
- **[deferred] Structured-store accessors** for the new numeric sources (cast string
  numerics to float): highest value are `cms` spending price and `nadac` acquisition cost
  (direct peak-sales net-price inputs), then `cdc` prevalence.

### Text / retrieval-suitability audit (all deferred, recommendation-only)

- **P1 — EDGAR.** Only the most-recent ~5 filings get text (`primary_text_count`), so older
  8-K/10-K docs are silent (0 chunks); the ones with text are 250+ chunk monoliths dominated
  by risk-factor boilerplate that repeats quarter-over-quarter. Recommend section-segmenting
  filings (Item 1A, MD&A, notes), down-weighting the repeated boilerplate, and lazy/expanded
  text fetch so older filings aren't invisible.
- **Chunker does not actually respect paragraphs** despite its docstring; it splits on raw
  word count and can break mid-sentence. Either fix the packing or correct the docstring.
- **P2 — NADAC / P3 — CDC.** Numeric stubs whose bodies lack the words an analyst searches
  ("cost", "acquisition", "price", "prevalence"); prepend a descriptive sentence, and collapse
  near-duplicate rows (per-NDC/date, per-state).
- **P4 — openFDA FAERS.** Many low-signal one-report stubs plus co-reported-drug token noise;
  consider an aggregated top-reactions doc instead of one-per-report.
- **P5 — Preprints HTML remnants.** Europe PMC `abstractText` carries inline HTML
  (`<h4>ABSTRACT</h4>`) that leaks junk tokens; strip tags before building text.
- **P6 — EDGAR financial tables** flatten into label-detached number runs; a structure-aware
  table pass would help line-item queries.
- **P7 — PubChem synonym registry-code soup** dilutes chunks; keep human/trade names, drop
  registry ids. Low priority.
- Best-in-class (no change): clinicaltrials and pubmed (clean labeled prose, one chunk each).

## Eval + quality improvement backlog (from the 5-company grading + Track A)

Grading of all five memos (Sonnet judge, uncalibrated) and the Track-A judge validation
surfaced these. The judge is trustworthy on direction (binary faithful/unfaithful 0.88,
known-bad discriminates) but lenient on plausible mismatches and noisy on exact verdicts,
so treat the decimals as directional until the calibration pass.

- **PoS overreach (highest-value quality fix).** PoS is the weakest faithfulness module
  (0.74-0.88, KYMR/COGT lowest); its claims assert more than the cited evidence shows.
  Apply the atomic + evidence-only discipline that took MoA to ~1.0, then re-grade with
  `eval --type memo`.
- **peak_sales grounded claims are unfaithful (~0).** When peak_sales cites evidence it
  cites tangential epidemiology/context that does not support the specific commercial
  claim (thin 2-4 claim samples). Stop grounding commercial claims on loosely-related
  evidence, or tighten what counts as support.
- **ABVX / foreign-filer regulatory (0.69).** Thinner 20-F/6-K regulatory evidence leads
  to overreach; handle FPI filings' regulatory content specifically.
- **Analytical quality trails faithfulness (rubric 0.5-0.8).** Even faithful modules read
  correct-but-not-sharp, price especially (0.62). A specificity/calibration prompt pass.
- **Retrieval levers, now measurable via hit@1.** The de-saturated eval shows headroom in
  paraphrase cases where a mechanism term pulls the wrong asset's trial, exactly the
  vocabulary-mismatch case for **dense embeddings + a reranker**. This is the evidence that
  finally justifies revisiting the deferred embeddings decision; add them behind the
  existing `Embedder`/rerank interfaces and measure the hit@1 lift.
- **Judge calibration (the trust anchor).** Label `evals/calibration/sample.json` (40
  claims) and run `calibrate --score` for judge-vs-human agreement + kappa; only then are
  the grade decimals trustworthy.
- **Reliable abstention needs a calibrated scorer.** Hard-negative scoring uses a coarse
  raw-BM25 top-score threshold because BM25 scores of present vs absent queries overlap
  (~15). A normalized/reranker/dense confidence would make abstention (and the hard-negative
  eval) sharp.
- **Human verification of drafted gold** (retrieval incl. the new hard cases, extraction).

## Web app, valuation, and figures (2026-09-10)

Parked during the unified-web-app build. The app runs end to end and is demoable; these
are conscious deferrals, not blockers.

- **Implied-PoS direction signal (not built, read-side).** The dashboard's "Lead-asset
  coverage" (rNPV / market cap) was reframed as a value *floor*, so it deliberately does
  not justify the long/short/neutral call. The data that would justify direction is the
  gap between our evidence-grounded PoS and the PoS the price implies. It is computable at
  read time with no recompose: `unadjusted NPV = rNPV / our_pos`, `implied_pos =
  enterprise_value / unadjusted NPV`; direction falls out of `our_pos` vs `implied_pos`.
  Slots into the new top "call" block next to the variant view. Caveat: market cap prices
  the whole company, so `implied_pos` on the lead asset alone often exceeds 100% (read it
  as an upper bound, and where it does, the finding is that the price requires the pipeline
  to carry real value).
- **KYMR / COGT peak-sales outliers (deferred; the Tier-B valuation).** After all the
  epidemiology fixes, three of five sit in a sane coverage band but two do not: KYMR ~182%
  (peak ~$55B, single-indication AD but gross pre-rebate pricing and ~20% penetration of
  all moderate-to-severe AD) and COGT ~1.2% (peak understated, grounded only on rare
  advanced systemic mastocytosis, ignoring non-advanced SM + GIST). The fix is to size the
  lead *asset* across its main indications, apply a gross-to-net haircut, and cap
  single-drug penetration (coordinated with the rNPV margin step so margin is not
  double-counted). We chose the honest reframe over building this. Needs a recompose. This
  is the multi-*indication* case; a multi-*drug* sum-of-parts (e.g. PRAX's three programs)
  is a further step that would also need a multi-drug page.
- **Real chart-image ingestion for the five coverage companies (not built).** The figures
  the memo inserts/annotates for ABVX/KYMR/PRAX/IMVT/COGT are labeled-synthetic stand-ins
  built from each company's real reported numbers (source URLs are genuine, `synthetic:true`
  in the manifest), because document ingestion pulls text only and no clean public chart
  image was downloadable (e.g. ABTECT results are HTML tables). The brief wants real figures
  taken from company presentations. Fix: fetch IR-deck / paper PDFs and extract the chart
  images per claim, best-effort (some companies disclose results as tables with no chart).
  Overlaps "IR deck scraping + figures (not built)" above. The Data > Figures tab already
  lists the *real* ingested figure corpus we do have (6 Stage-1 slides + ~409 harvested
  FDA/PMC figures); those are just not tied to the five tickers.
- **Unify the KYMR figure path (small).** KYMR is not in the `memo.figures.ingest` REGISTRY;
  its figures come from a bundled `memo/figures/samples/KYMR.json` instead of an ingested
  `data/KYMR/figures/manifest.json`. Fold KYMR into the ingest registry so all five flow
  through one path.
- **Harvested-corpus figure captions (small).** FDA corpus figures usually have an empty
  caption and a generic auto-title on disk, so the Figures tab falls back to the first line
  of the figure's context (often a document header). Real, but weak. A caption pass over the
  harvested corpus (or reading the nearby figure legend) would sharpen those cards.
- **Eval-framework consolidation (deliverable #4).** Evals exist piecemeal (retrieval,
  epi-retrieval, epi-grounding, figure extraction/scoring, the faithfulness/grading backlog
  above). Consolidate into one coherent framework with the brief's documentation: what was
  built, what is unreliable, what a further month would fix, and the cost of one run.
