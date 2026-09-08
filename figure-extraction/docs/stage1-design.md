# Stage 1 — Figure Extraction: Design Doc

Status: design + working prototype. This is the internal design behind the Stage 1 deliverables (written approach, extractions, evaluation plan, prototype). It is more detailed than the two-page approach we submit; the submission is a distillation of this. Sections 1 to 8 are the design; section 9 records what is actually built and the results so far; section 10 is the repository map; section 11 the resolved and open questions. The prototype is dependency-free Python (standard library only) and is test-covered (30 unit tests).

## 1. What we are actually building

The task is to read specified numbers off six oncology figures supplied as flat rasters with no text layer, and to return each value with the method that produced it and a confidence. Four of the six had their summary values stripped, so the number has to come from the graphic, not a caption.

The brief grades three things above raw accuracy:

- Every value carries its **method** and a **confidence**.
- The reasoning is **inspectable**. The system gets walked through and pressure-tested for an hour.
- The account of failure is **candid**. A clear map of where the approach breaks is worth more than a claim that it doesn't break.

So the target is not "it got the numbers right." It is "here is exactly how each number was obtained, here is how sure we are and why, and here are the reads we cannot do well." Both the engine and the interface are built to make that legible.

## 2. Core design decisions

1. **One approach loses.** The six figures are not one problem. A router classifies the figure, then dispatches to an extractor tuned to that type.
2. **Split the work by what each tool is good at.** Vision-language models (VLMs) are strong at reading text, transcribing tables, classifying structure, and applying a stated rule. They are weak at eyeballing a precise position on an axis. Geometric reads want pixel measurement against a calibrated axis. The hybrid is per-figure-type, not a blend on every figure.
3. **Confidence is mechanical, not self-reported.** It comes from measurement error, agreement between two independent reads, and hard axis-resolution limits. It is reported as an interval, not a scalar, so calibration is measurable.
4. **Precompute the six, keep the engine callable.** The demo is a deterministic, defensible viewer over precomputed output. The engine stays a callable pipeline so a fresh image can be dropped in live if we want that.
5. **Depth and candor over coverage.** All six are attempted, but every read is labeled into a tier: runs unattended, needs analyst review, or cannot be resolved from the figure.

## 3. Engine architecture

```
image
  -> preprocess (deskew, detect plot area, axis region, legend region)
  -> router (VLM): figure type + basic structure
  -> per-type extractor
        - axis calibration (find ticks with known values, build pixel->data transform)
        - VLM read (structure, labels, an independent value estimate)
        - CV measurement (feature location in pixel space -> data space)
        - reconcile the two reads
  -> confidence model (error propagation + agreement + axis-resolution cap)
  -> structured output: value, interval, method, confidence, annotated overlay, intermediate reads
```

Every stage writes to a structured record per value. The annotated overlay (the arrow or box on the feature that was measured) is produced by the engine, not added by hand.

### 3.1 Per-figure-type approach

| # | Figure | Primary method | Why | Expected confidence |
|---|--------|----------------|-----|---------------------|
| 1 | Kaplan–Meier | CV measurement against calibrated axes; VLM cross-read | Median is where the step curve crosses 0.5; landmark is the curve height at a given x. Both are geometric. | Medium-high. At-risk table anchors the x-axis. |
| 2 | Waterfall | CV bar detection + calibrated y-axis; VLM for subgroup/legend | Bar depths and counts past a threshold are geometric and countable. | Medium-high, if bars are separable. |
| 3 | PK log-scale | CV on a log-transformed axis; VLM cross-read | Reads sit between log decades with no minor gridlines. Interpolation on a log axis is the hard part. | Low-medium. Missing minor ticks cap it by design. |
| 4 | Forest plot | CV marker + whisker detection against calibrated axis | HR is the marker x-position; CI is the whisker span. Geometric. "Crosses 1" is a categorical read off the same measurement. | Medium. Small CIs strain pixel resolution. |
| 5 | Raster table | VLM transcription with denominator attribution | This is OCR of a structured table. VLM is strong. Care needed on which denominator each rate is drawn from. | High on cells, with a denominator-attribution check. |
| 6 | Spider -> PFS | VLM applies a stated event/censoring rule; produces a curve | No published answer exists; this is an analyst construction. The deliverable is the rule and a defensible range, not a single number. | Reported as a range with the rule, not a point. |

The split is the honest part of the design: figures 5 and 6 are reading and reasoning tasks where the VLM leads; figures 1 to 4 are measurement tasks where pixels lead and the VLM is a cross-check.

**Built so far (see section 9):** the VLM extractor + router, and the CV measurement for the waterfall (fully calibrated to the real slide) and Kaplan–Meier (validated on synthetic, real-slide calibration pending). Forest and PK CV measurement are designed but not yet written; they slot in behind the same `CvExtractor` shape.

## 4. Confidence model

Confidence is built, not asked for. Three inputs:

1. **Measurement error propagation.** Axis tick spacing, line thickness, and marker size set a pixel-level uncertainty. That propagates through the pixel-to-data transform into an interval on the value. A thick curve on a coarse axis produces a wide interval honestly.
2. **Agreement between two independent reads.** The VLM estimate and the CV measurement are produced separately. Tight agreement raises confidence; divergence lowers it and flags the value for review. This is the signal that survives into production, where no answer key exists (see 5.3).
3. **Axis-resolution cap.** The figure sets a hard ceiling. A log axis with no minor gridlines (figure 3) mathematically limits how precisely a between-decade value can be stated. The model is not allowed to report more precision than the axis carries. This directly prevents hallucinated precision.

Output is an **interval with a stated confidence level**, not a bare number plus a vibe. Reporting intervals is what makes calibration measurable in the eval.

### 4.1 Triage tiers

Every value lands in one tier, and the tier is shown in the interface:

- **Unattended (high confidence):** tight interval, two reads agree, axis supports the precision.
- **Analyst review:** wide interval, or the two reads diverge, or the axis caps precision below what the question needs.
- **Cannot resolve:** the figure does not carry the information (for example, a range the spider plot cannot pin down). Stated as such rather than guessed.

This tiering is the direct answer to the brief's question about what runs unattended versus what needs review.

## 5. Evaluation

The brief's real question is "how would it be established that the system works, and what would be measured to know when it does not." Two facts make this hard:

- The six figures had their summary values removed, so at first glance there is no answer key.
- On a real unseen figure in production there will **never** be an answer key. The eval cannot be a one-time scorecard. It has to validate a signal we can use when no truth exists.

### 5.1 Ground truth, three tiers

1. **Synthetic replots (the backbone).** For the four measurement figures, generate figures from data we control, render them in a corporate-slide style, strip the summary values, and score against the numbers we put in. This is the only way past n=6 into error *distributions*, and it lets us sweep difficulty on purpose: axis ranges, log versus linear, gridline density, resolution, compression, crossing curves, censoring density. Known risk: a realism gap, since synthetic figures can be cleaner than real slides. We name that gap rather than hide it.
2. **Recovered public values (the credibility anchor).** These are real companies, and the stripped summary values exist in the public domain. The strongest sources put the figure and its numbers in the same document: PubMed Central open-access papers (curves in the figure, medians/HRs/CIs/rates in the results text), FDA advisory-committee briefing docs and drug labels (regulatory-grade figures and numbers together), and a ready-made annotated forest-plot dataset (COCHRANEFOREST, ~200 plots) for figure type 4. This is used to *score*, never for the system to look up.
3. **Expert manual read (for the interpretive ones).** Figure 6 has no published answer. It is scored against a careful human read (WebPlotDigitizer plus Guyot reconstruction for KM) and, more importantly, against inter-reader agreement. If two careful humans disagree by two months on the implied median, the system cannot be expected to do better, and that spread is the irreducible confidence floor.

### 5.2 Real held-out set

A curated ~30 to 50 examples per figure type from PMC + FDA + SEC EDGAR investor decks (Exhibit 99.1 8-Ks are the actual production distribution: compressed, brand-colored, footnote-cluttered). Its job is generalization and face credibility. The **synthetic-to-real accuracy gap is reported as the honest measure of production readiness.**

Source notes: ClinicalTrials.gov holds structured result tables, not figures, so it is a ground-truth cross-check (and a natural pairing for figure type 5), not a figure source. PK log-scale curves (type 3) are the scarcest in the wild and live in clinical-pharmacology OA journals and FDA clin-pharm reviews, so type 3 leans hardest on synthetic.

### 5.3 The keystone result

In production there is no truth, so triage rests on **agreement between the VLM read and the CV measurement.** The labeled eval's real job is to earn the right to trust that proxy: on the synthetic corpus, plot dual-read agreement against actual error. If tight agreement reliably means small error and divergence reliably means large error, then divergence is a trustworthy "send to analyst" trigger on figures never seen before. This is the bridge from "scored well on six" to "knows when it is wrong on the seventh." It headlines the walkthrough.

### 5.4 Metrics, per figure family

- **Continuous reads** (KM median in months, PK nM, bar depth %): mean absolute and relative error, and interval coverage (does the 90% interval contain truth 90% of the time).
- **Proportions** (PSA50 = k/n): exact match on numerator and denominator. The brief asks for the denominator explicitly, so a right proportion on the wrong denominator fails.
- **Table transcription** (fig 5): cell-level exact-match rate and correct denominator attribution.
- **Categorical set** (forest "which subgroups cross 1"): precision and recall on the set.
- **Router:** confusion matrix on figure type. Cheap, expected near-perfect, but it gates everything, since a misroute produces confident garbage.
- **Headline visual:** a reliability diagram plus expected calibration error across all continuous reads. This answers "how would you know when it fails" better than any accuracy table.

### 5.5 Two evals people skip

- **Failure taxonomy, not just error magnitude.** Categorize misses: axis miscalibration, wrong feature (wrong arm/bar/row), unit error, denominator error, hallucinated precision, refusal error. A calibration miss and a wrong-arm miss need different fixes; lumping them into one number hides the actionable part.
- **A refusal eval.** Seed the corpus with cases that should be declined (too low resolution, genuinely ambiguous, off-distribution) and measure whether the system declines them, and whether it wrongly declines good ones.

### 5.6 The quantity-key contract

Matching a prediction to the right ground-truth value is its own problem: free-text names do not align (an extractor's "median PFS, treatment arm" versus a database's verbose outcome title). So both sides tag every value with a canonical `quantity_key` (e.g. `median_pfs.ozekibart`, `hazard_ratio.idh_wildtype`), and the matcher aligns by lookup. This is the contract the extractor, the gold, and the scorer all conform to. Where keys do not line up, an LLM-judge matcher aligns by meaning as a fallback; it only aligns labels, the deterministic scorers still grade the numbers, so no model touches a score. The vocabulary (27 keys across the six figures) is `evaluation/keys.py`.

## 6. Presentation layer

A results viewer over the engine's structured output. For each figure it shows: the original image, the engine's annotated overlay, the extracted value with its interval, the method, the confidence with its reason, and the intermediate reads. A review queue surfaces the analyst-review and cannot-resolve tiers.

Precomputed first, so the walkthrough is deterministic and every number is defensible. The engine stays callable so a fresh image can be dropped in live as an optional flourish. We do not need an agent framework for six figures; a router plus extractors plus a viewer is cleaner to defend, and it keeps the substance testable independently of the UI.

Rejected: a full data platform. It signals engineering effort in the wrong direction for six figures on a short timeline, and the brief warns against mistaking engineering for analysis.

## 7. Where this fails (stated on purpose)

- **Native resolution is modest** (~200 ppi; these were slide graphics). Upscaling adds no real detail. This caps fine reads, especially log-axis interpolation on figure 3.
- **Figure 3 has no minor gridlines.** Between-decade reads carry real uncertainty; we report a range, not a point.
- **Figure 6 has no ground truth.** Only consistency and range-coverage can be scored, and we say so.
- **Recovered "true" values sometimes disagree across sources.** Ground truth for the real six is itself a small adjudication problem.
- **n=6 is statistically nothing.** The synthetic corpus carries the statistical load, and its realism gap is a stated limit.
- **Two of the six figures carry synthetic values.** Recovering the real published numbers (section 9) confirmed figures 1, 2, 5, 6 against public sources but found that figures 3 (PK dose-level values, "20 pM" threshold) and 4 (per-subgroup HRs) name real programs whose specific requested numbers are not publicly disclosed and appear constructed for the exercise. Those reads can only be scored against a careful manual digitization, not a public source, and are flagged unverified in the gold.
- **Conflicting disclosure is real, even inside one figure.** Figure 5's table shows a QUILT 3.032 24-month DOR of 40%, while the later published figure is 53.2%. The gold keeps 40% because the task is reading the figure, and the conflict is recorded. This is the "figures frequently disagree" problem in miniature.

## 8. Cost and tooling

The brief asks for the cost of one run, so model calls per figure are tracked in the extractor. Deliberate choice: the prototype is **standard-library only**, no OpenCV, numpy, or Pillow. CV reads pixels through a small in-house PNG codec (`evaluation/cv/png_io.py`) and measures in plain Python. The only external dependency is the model API itself (Anthropic, credentials from the environment at run time), reached over raw HTTP. This keeps a fresh clone runnable with nothing to install, which matches the execution model in the brief, and keeps the CV measurement fully in-house and inspectable. External services used: the Anthropic API (extraction, judge matching), and, for corpus building, the public read APIs listed in section 10.

## 9. What is built, and results so far

The prototype exists as two packages (`corpus/` for the eval-data ingestion, `evaluation/` for the harness and extractors) plus the extracted figures. 30 unit tests pass. What runs today:

- **The eval harness** end to end: `gold figure -> extractor -> matcher -> parse -> deterministic scorer -> aggregate -> report`. Numeric scoring is deterministic (seven family scorers); the LLM judge is confined to matching and the interpretive figure. Runs against a stub extractor so the scoring was validated before any real extractor existed.
- **The quantity-key contract** and **gold set** for all six figures (`evaluation/gold/`), reconciled against recovered public values, with `verified` flags separating confirmed answers from eyeballed reads.
- **A VLM extractor + router**, and **CV measurement** for the waterfall and Kaplan–Meier.
- **The keystone made real:** for geometric figures the two reads are now a VLM read and an independent CV measurement (not two samples of one model), so read-disagreement is a genuine cross-check.

Two results worth recording:

- **CV corrects the eyeball where it fails worst.** On the real waterfall slide, the manual PSA90 read was 10%; the true published value is 26%; the CV pixel measurement recovers 26% exactly (and PSA50 72% versus 73%, deepest −99%). This is the design thesis (pixels beat eyeballs on geometric reads) validated against recovered public ground truth on a real figure, not a synthetic one.
- **The metrics are diagnostic, not decorative.** Building the CV extractor surfaced a real bug (bar-segmentation parameters were not passed through from config, merging densely-packed bars and collapsing the proportions); the eval numbers caught it. The keystone and calibration metrics likewise behave correctly on the stub's known error structure, including the honest fact that read-agreement only catches *random* error, not systematic bias where both reads are wrong together.

Remaining prototype work, in priority order: real-slide calibration configs for KM (fig 1), then forest and PK CV measurement; the synthetic generator for the statistical eval; the real held-out set; and the results viewer. The graded core (approach, extractions, evaluation) is in place; the prototype is depth-first by design.

## 10. Repository map

- `assets/figures/` — the six figures, extracted from the brief PDF.
- `corpus/` — eval-data ingestion. `base.py` (figure-centric records, storage, HTTP), `sources/` (PMC, ClinicalTrials.gov, COCHRANEFOREST, FDA, EDGAR decks, conference), `pdf_figures.py` (poppler-backed PDF figure/text extraction), `cli.py`. Public read APIs only; ClinicalTrials.gov validated live.
- `evaluation/` — the harness. `keys.py` (quantity-key contract), `types.py`, `parse.py`, `scorers.py` (deterministic), `match.py` (exact-key + LLM-judge seam), `extractors.py` (stub + interface), `extractor_vlm.py` (VLM + router), `llm.py` (stdlib model client), `metrics.py`, `run.py`, `gold/` (reference values + recovered_values.md), `cv/` (PNG codec, calibration, waterfall, Kaplan–Meier, CV/combined extractors), `tests/`, `README.md`.
- `docs/` — the briefs and this design.

## 11. Resolved and open questions

Resolved this iteration:

- **Interval confidence over scalar: yes.** Contact with the real figures confirmed it (a single figure spans near-exact labeled reads and soft interpolations), and it is what makes calibration measurable. Implemented.
- **Effort on recovering real values: done for the six.** Figures 1, 2, 5, 6 confirmed against public sources; 3 and 4 found to be likely synthetic. This is the credibility anchor and it surfaced the synthetic-values and conflicting-disclosure findings in section 7.

Still open:

- **What claim we make about unseen figures**, and how we avoid overfitting the per-figure CV calibration to these exact six. The synthetic corpus and the real held-out set are the intended guard; the calibration configs are the overfitting risk to watch.
- **Live drop-in in the demo, or precomputed only.** Leaning precomputed spine with live as optional; unchanged.
- **The whole-panel versus subgroup quantity nuance** exposed by the waterfall: color-gating to the ≥2 mg subgroup answers the proportion questions correctly but makes "leftmost/deepest bar" measure only that subgroup. Those quantities need a combined-color mask.
