# Triage and Accuracy: What Runs Unattended, What Needs Review, and the Ceiling

Draft for the Stage 1 "approach" deliverable. This answers the three estimates the brief asks for: the share of extractions that could run unattended at high confidence, the share that need analyst review, and the accuracy ceiling to expect. It is built from the real six-figure run (`data/eval_report.json`, verified-only, samples=1) plus the confidence model in `docs/stage1-design.md` section 4.

Read the candor note at the end first if you only read one thing: this rests on n=6 and design reasoning, not a large sample.

## The run this is built on

Within-tolerance rate by figure type, from the scored (non-interpretive) quantities:

| Figure type | Scored n | Within tolerance | Notes |
|---|---|---|---|
| Table | 4 | 100% | OCR of a structured raster |
| Kaplan-Meier | 4 | 75% | medians pass, landmark probabilities strain |
| PK log-scale | 4 | 75% | passes on labeled/bracketed reads, one large miss |
| Waterfall | 6 | 50% | proportions need the CV cross-check |
| Forest | 3 | 0% | small CIs, values not publicly verifiable |
| Spider | 1 | 0% | interpretive; a point read here is the wrong target |

Two whole-system numbers matter for triage. Calibration ECE is 0.174, so reported confidence is loosely tied to accuracy but not tight. More importantly, the correlation between dual-read agreement and actual error is r = 0.095 on n = 16. The triage rule leans on agreement predicting error, and on this tiny sample that signal is close to flat. See the candor section for why, and why that is not yet a reason to abandon it.

## 1. Triage by figure type

The confidence model routes each value on three inputs: agreement between the independent VLM read and the CV pixel measurement, propagated measurement error (interval width), and a hard axis-resolution cap. The tier follows from whether the read is a transcription, a geometric measurement, or an interpretation.

**Table (fig 5): mostly unattended.** This is OCR of a structured raster and the VLM is strong at it. 100% within tolerance on the run. The one thing that keeps it from fully unattended is denominator attribution: a right rate on the wrong denominator fails, and the table shows several denominators (the NCT02773849 any-time vs 12-month population split). The two reasoning quantities on this figure (why the denominators differ, which DOR estimator is comparable) are analyst tasks, not cell reads. So: cells unattended, denominator logic and the two interpretive rows to review.

**Kaplan-Meier (fig 1): mostly unattended for medians, review for landmarks.** The median is where the step curve crosses 0.5 and the x-axis is anchored by the at-risk table, so it is a well-constrained geometric read. Both medians verified against the public topline (5.52 and 2.66 months) and passed. The 6-month landmark probabilities are a finer read of curve height against a 0.05 tolerance, close to the resolution limit, and are eyeball values with no public confirmation. Medians unattended, landmarks to review.

**Waterfall (fig 2): needs review, resolved by the CV cross-check.** This is the clearest hybrid case. The VLM alone scored 0/4 on the value questions because the bars show bare percentages with no denominators, so it could not build the proportions. The CV pixel measurement read PSA90 = 26% exactly (against the recovered public 26%, where the earlier manual eyeball of 10% was well off) and PSA50 within one point. Counts (RECIST n plotted, the 8/27 beyond -30%) are countable and robust enough to run unattended. The proportions and individual bar depths route to review specifically because the two reads diverge, which is the intended trigger; they become trustworthy once the CV measurement is present and the subgroup color-mask nuance is handled. So: counts unattended, proportions and bar depths through the CV cross-check before an analyst signs off.

**PK log-scale (fig 3): needs review, some cannot-resolve.** Labeled and bracketed reads are fine: the MTD threshold is read straight off the figure's own "20 pM" label, and the 1 nM crossing is bracketed between two sampled timepoints, both categorical-ish and robust. The point concentrations are the hard part. The axis is log with no minor gridlines, so a between-decade value carries real, irreducible uncertainty, and the model is capped from reporting more precision than the axis carries. The fold-multiple derived from an eyeballed peak was far off (and the underlying values appear synthetic). Labeled/bracketed reads lean unattended, between-decade point reads to review, the derived fold-multiple cannot be fully resolved from the figure.

**Forest (fig 4): needs review.** Geometrically clean in principle: HR is the marker x-position, the CI is the whisker span, and "crosses 1" is a categorical read off the same measurement. In practice small CIs strain pixel resolution at native resolution, the run scored 0/3, and the per-subgroup HRs are not publicly disclosed (likely synthetic), so there is no external check. The CV marker/whisker extractor is designed but not yet built; until it is and its intervals are validated, every forest value routes to review.

**Spider (fig 6): cannot be fully resolved as a point.** No published answer exists; the honest deliverable is the event/censoring rule and a defensible range, not a single number. The one numeric quantity scored here (implied 6-month PFS) missed badly, which is expected, because the target itself is a range. The router also misclassified this figure as a waterfall (the one router miss, 5/6), which is a second reason not to trust an automated point read here. Interpretive throughout: state the rule and range, route to analyst.

## 2. Overall proportions

Classifying all 27 requested quantities (22 scored, 5 interpretive) into the three tiers using the reasoning above:

| Tier | Count | Share | What lands here |
|---|---|---|---|
| Unattended (high confidence) | 10 | ~37% | table cells (4), KM medians (2), PK threshold + crossing (2), waterfall counts (2) |
| Analyst review | 12 | ~44% | KM landmarks (2), waterfall proportions + bar depths (4), forest (3), PK between-decade point (1), table denominator/estimator reasoning (2) |
| Cannot fully resolve | 5 | ~19% | spider interpretive set (4), PK derived fold-multiple (1) |

Headline estimate: roughly a third of extractions could run unattended at high confidence, a little under half need analyst review, and about a fifth cannot be pinned to a point value from the figure at all. Rounding to defensible bands: 35-40% unattended, ~45% review, ~20% cannot resolve.

**The triage rule that produces this.** A value runs unattended when the VLM read and the CV measurement agree, the propagated interval is tight, and the axis supports the requested precision. It routes to review when the two reads diverge, the interval is wide, or the axis caps precision below what the question needs. It is marked cannot-resolve when the figure does not carry the information (an interpretive range, or a derivation with no anchor). The divergence trigger is what survives into production, where no answer key exists: on an unseen figure, disagreement between the two independent reads is the only "I might be wrong" signal available.

Two honest adjustments to keep in view. The unattended share is optimistic in that it assumes the CV extractors for waterfall, forest, and PK are built and calibrated; only waterfall and KM CV exist today. And it is conservative in that several review-tier values (the waterfall proportions especially) will graduate to unattended once the CV cross-check confirms them, since the CV read itself is accurate. The split is a snapshot of intended routing, not a claim about the current partial build.

## 3. Accuracy ceiling

Ceiling means the best within-tolerance rate achievable given the fixed constraints of these figures, not what the current prototype scores.

**Table: high, ~95%+.** Clean structured raster, VLM transcription. The only ceiling is transcription edge cases and denominator attribution, both checkable.

**Kaplan-Meier: high on medians (~90%), moderate on landmarks (~75-85%).** Medians are anchored by the at-risk table. Landmark probabilities are capped by curve thickness at native ~200 ppi; below roughly 0.05 in probability the read is at the resolution floor.

**Waterfall: moderate-high with CV, ~75-85%.** Proportions and counts are recoverable (CV hit 26% and 73% against public truth). Individual bar depths are capped by resolution and by the subgroup color-mask question. Without the CV cross-check, VLM-alone accuracy on the value questions is near zero, so the ceiling depends on the hybrid being in place.

**PK log-scale: low-moderate on point reads, high on labeled/bracketed.** The log axis with no minor gridlines is a hard mathematical cap on between-decade precision, which is why tolerances here are set in fold, not absolute. Labeled thresholds and bracketed crossings can be near-exact; a between-decade point concentration cannot beat roughly a 1.3 to 1.6 fold band. Report ranges, not points.

**Forest: moderate, ~60-75% if the CV extractor lands.** HR marker position is recoverable to roughly a few hundredths; CI endpoints are the ceiling, because small CIs span only a few pixels at native resolution. No external ground truth for these subgroups, so the ceiling is a claim about geometric precision, not verified accuracy.

**Spider: no numeric ceiling.** No ground truth exists. The achievable target is a defensible range plus the stated rule, and the irreducible floor is inter-reader agreement: if two careful human readers disagree by two months on the implied median, the system cannot be expected to do better. Score consistency and range-coverage, not point accuracy.

**Overall ceiling statement.** On the transcription and labeled reads (table, KM medians, PK/waterfall labeled and counted values), expect high accuracy, 90%+ within tolerance. On the geometric measurement reads with the CV cross-check in place (waterfall proportions, KM landmarks, forest points), expect moderate-high, roughly 75-85%, bounded by native ~200 ppi. A hard residual subset ceilings well below that and should be reported as ranges or sent to an analyst rather than forced to a point: PK between-decade concentrations (log axis, no minor gridlines), forest CI endpoints (pixel-limited), and every interpretive quantity (no ground truth). System-wide, a realistic ceiling is about 80% within tolerance on the measurement-and-transcription majority, with the understanding that the remaining fifth is genuinely not a point-estimation problem.

## 4. Candor: what this estimate does and does not rest on

- **n = 6.** These proportions and ceilings come from six figures and the design's per-type reasoning. That is enough to see the structure (transcription easy, geometry medium, log-axis and interpretive hard) but not enough to state error distributions. The synthetic corpus in the design (section 5.1) is what is meant to carry the statistical load and turn these into distributions; it does not exist yet, and it has a named realism gap because synthetic figures are cleaner than real slides.

- **The routing signal is not yet validated at scale.** Triage leans on dual-read agreement predicting error. On this run that correlation is r = 0.095 (n = 16), essentially flat. Two reasons: the sample is tiny, and agreement only catches random error, not the case where both reads are wrong together (systematic bias). The waterfall is the cautionary example, where the manual eyeball and a naive read could both have been low. Earning the right to trust the divergence trigger is exactly what the synthetic eval is for; today it is an assumption, not a demonstrated result.

- **Calibration is loose.** ECE is 0.174. Reported confidence tracks accuracy in direction but should not be read as a precise probability yet.

- **Two figures have no public ground truth (3 and 4).** The PK dose-level values and the forest subgroup HRs name real programs but the specific numbers appear constructed for the exercise. Their reads can only be scored against a careful manual digitization, so their tier placement rests on the resolution argument, not on measured accuracy.

- **The build is partial.** The unattended share assumes CV extractors for waterfall, forest, and PK. Waterfall and KM CV exist; forest and PK CV are designed but not written. Read the proportions as intended routing under the full design, not the current prototype's live behavior.

Assumptions made explicit: quantities are weighted equally (each of the 27 counts once); the CV cross-check is assumed present and calibrated for the measurement figures; verified public values are treated as truth where they exist; and tier boundaries follow the confidence model's three triggers (agreement, interval width, axis cap) rather than a tuned threshold, because there is no sample to tune one on.
