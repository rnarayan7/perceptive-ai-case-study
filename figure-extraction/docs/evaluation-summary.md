# Evaluation: how we know it works, and how we know when it doesn't

*Draft for finalization. Numbers are from the current six-figure run (`data/eval_report.json`); the honest limits are stated at the end.*

The hard part of evaluating this system is that there is no answer key. The six brief figures had their summary values stripped, and a real figure in production will never arrive with truth attached. So the evaluation cannot be a one-time scorecard. It has to do two things: establish that the extractor is accurate where we can check it, and validate a signal we can trust when we cannot check it at all.

## 1. Establishing that it works

Accuracy is only meaningful against ground truth we didn't derive from the system. We build it in three tiers, each covering a weakness of the others.

**Synthetic replots are the backbone.** For the four measurement figures we generate plots from data we control, render them in a slide style, strip the summary values, and score against the numbers we put in. This is the only route past n=6 into error *distributions*, and it lets us sweep difficulty deliberately: axis range, log versus linear, gridline density, resolution, curve crossing, censoring density. The known cost is a realism gap, since a generated figure can be cleaner than a real slide. We measure that gap rather than hide it (see below).

**Recovered public values are the credibility anchor.** These are real programs whose stripped numbers exist in the public record: PMC open-access papers (the curve and its medians/HRs/rates in one document), FDA briefing docs and labels, and a ready-made annotated forest-plot set for figure type 4. We use these to *score*, never for the system to look up. This is what makes a number defensible to someone outside the project.

**Hand-labeled real figures carry it to scale.** `corpus_eval.py` is the path from a labeling effort to real metrics. It loads figures a human verified by hand, runs a demanding free-form extractor on each image, aligns the predictions to the labels, and scores deterministically. The interpretive figure (no published answer) is scored against a careful manual read and, more usefully, against inter-reader agreement: if two careful humans disagree by two months on the implied median, that spread is the confidence floor, not a target to beat.

**Why the scoring is non-circular.** Matching a prediction to the right gold value is a language problem (an extractor's "median PFS, treatment arm" versus a database's verbose title). We let an LLM judge solve *only that alignment*. Once a prediction is matched to its gold value, the number is graded by a deterministic scorer: plain arithmetic, one per value family, exact and reproducible. No model ever touches a score. The cleanest signal of all is the subset whose gold came from a recovered public number, which no model produced on either side.

## 2. Measuring when it fails

A single accuracy figure hides the part you can act on. We measure five things.

**Per-family accuracy.** Continuous reads (KM median, PK concentration, bar depth) report mean absolute and relative error. Proportions require exact match on *both* numerator and denominator, because the brief asks for the denominator and a right rate on the wrong base is a fail. Table cells report exact-match rate plus correct denominator attribution. Forest "which subgroups cross 1" reports set precision and recall. The router reports a confusion matrix, since a misroute produces confident garbage downstream.

**Calibration (ECE).** Every value carries a stated confidence, so we bin predictions by confidence and check whether stated confidence matches observed accuracy. Expected calibration error summarizes the gap. This answers "how would you know it's failing" better than any accuracy table: it tells you whether to trust the confidence the system reports.

**Interval coverage.** Values are reported as intervals, not points. We check whether the 90% interval actually contains truth 90% of the time. Under-coverage means the intervals are too narrow and the system is quietly overconfident.

**The error taxonomy.** Misses are categorized, not just counted: axis miscalibration, wrong feature (wrong arm/bar/row), unit error, denominator error, hallucinated precision, and refusal error. A calibration miss and a wrong-arm miss need different fixes; one number hides that.

**The keystone: does read-agreement predict error.** In production there is no truth, so triage rests on agreement between the two independent reads (VLM and CV measurement). The labeled eval's real job is to earn the right to trust that proxy: on the synthetic corpus, correlate dual-read agreement against actual error. If tight agreement reliably means small error and divergence reliably means large error, then divergence becomes a trustworthy "send to an analyst" trigger on figures never seen before. This is the bridge from "scored well on six" to "knows when it's wrong on the seventh." By construction it only catches *random* error; when both reads are wrong the same way, it stays silent, and we say so.

**The refusal eval.** We seed the corpus with cases that should be declined (too low resolution, genuinely ambiguous, off-distribution) and measure whether the system declines them, and whether it wrongly declines good figures.

## 3. Real results so far

The six-figure run scores the VLM extractor against verified public gold. Read it as a diagnostic, not a leaderboard: n=6 has no statistical power.

**What worked.**
- **Table transcription: within-tolerance 1.0**, zero cell error. OCR of a structured table is where the VLM is strongest, and it shows.
- **Kaplan-Meier: within-tolerance 0.75**, MAE 0.065 months. Median and landmark reads land close.
- **PK log-scale: within-tolerance 0.75** with a tiny median error, better than the "low-medium" the design predicted, though the missing minor gridlines still cap it and one read misses wide.
- **Router: 5/6 correct.** The one miss is the spider plot routed as a waterfall, which is a reasonable structural confusion and the figure the system should decline to point-read anyway.

**What didn't.**
- **Forest: within-tolerance 0.0**, MAE 0.44 on the ratio. The VLM cannot eyeball a hazard-ratio marker to the needed precision; this is exactly the geometric read that wants CV measurement, which is designed but not yet built for the forest plot.
- **Waterfall (VLM): within-tolerance 0.5.** The VLM read the deep bars poorly. The design thesis held: the CV pixel measurement corrected the worst eyeball error on the real slide (a manual PSA90 read of 10% against a true 26%, recovered as 26% exactly; PSA50 72% against 73%). Pixels beat eyeballs on geometric reads, validated on a real figure, not a synthetic one. Building the CV extractor also surfaced a real segmentation bug that the eval numbers caught.
- **CV beats the VLM on geometric reads, confirmed across four harvested waterfalls.** On four hand-labeled figures pulled from open-access papers (27 values), the VLM scored within-tolerance 0.22 and missed 16 values outright. CV on the same four figures nailed the reads the VLM cannot: extreme values (leftmost, deepest) landed within 1 to 4 percent on every figure, and negative-threshold bar counts were exact wherever bars were separable (camrelizumab 8 of 8 beyond both -30 and -50; anlotinib 20 beyond -30; chemo 9 beyond -30; PSMA 25 against a true 28 beyond -90). The clean two-color figure (camrelizumab, 9 bars) matched all six labeled values. CV's failure mode is density, not accuracy: when bars merge (54 and 185 per panel) the total count undercounts and the crowded positive region collapses, but it never returns a wrong value. Each figure carried a manual calibration cost (format conversion, two axis anchors, bar colors, plot region, legend and panel masks), which is why CV is a precision instrument aimed at the figures worth calibrating, not a scale play.
- **Calibration ECE 0.174.** Not honest yet. High-confidence reads (0.95+) are well behaved and low ones are cautious, but a mid-confidence band is clearly overconfident (stated ~0.56, actual 0.0). That band is where the reported confidence should not yet be trusted.
- **Interval coverage 0.80**, below the 0.90 target: intervals run slightly too narrow.
- **Keystone: pearson_r 0.095 on n=16.** Effectively no correlation. This is *not* the keystone validated; n=16 real values is far too few, and the signal is meant to be established on the synthetic corpus, which is not built. The number today only confirms the metric runs, not that read-disagreement predicts error.

**Limits, stated plainly.**
- **n=6 is not statistical power.** The synthetic backbone is designed but not yet built, so the error distributions and the real keystone test do not exist yet. This is the biggest gap between the design and what's proven.
- **Figures 3 and 4 have no public ground truth.** Recovering the real numbers confirmed figures 1, 2, 5, and 6 against public sources but found that the PK dose values and the per-subgroup HRs are not publicly disclosed and appear constructed for the exercise. Those reads can only be scored against a careful manual digitization, and are flagged unverified in the gold.
- **The keystone catches random error only.** Systematic bias, where both reads are wrong together, evades it by design. It is a triage signal, not a correctness guarantee.
- **Recovered "true" values sometimes disagree across sources**, so even the anchor is a small adjudication problem (figure 5 alone shows a 40% DOR in the figure against a 53.2% published later; the gold keeps the figure's number because the task is reading the figure, and records the conflict).

The honest summary: the deterministic scoring harness, the gold contract, and the metrics all work end to end and are already catching real bugs and real miscalibration. The measurement CV corrects the eyeball where it matters most. What remains before we could claim production readiness is scale, which the synthetic corpus provides, and the keystone validation that scale unlocks.
