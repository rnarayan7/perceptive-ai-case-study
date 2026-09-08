# Analysis Quality Rubric

> **DRAFT - pending analyst sign-off.** These dimensions and anchors have not been
> validated by a human analyst, and the LLM judge that applies them is **uncalibrated**:
> its 1-5 scores have not been checked against human scores on the same outputs. Treat
> everything here as directional until both the rubric and the judge are signed off.
> The scores do not gate anything yet.

## Purpose

Score an analysis module's output (an `AnalysisResult`: a summary plus grounded claims)
for analytical **quality**, not just faithfulness. Faithfulness asks whether each claim
is supported by its cited evidence. This rubric asks the softer question the desk
actually cares about: is this good analysis? It is designed to apply across modules
(MoA, PoS, regulatory, peak sales, price), not just one.

Applied by `memo.eval.quality.QualityEvaluator` with a Claude judge via
`ModelClient.complete_json`.

## Scoring

Each dimension is scored on an integer **1-5** scale with a one to two sentence
justification. The evaluator normalizes each score to 0-1 as `(score - 1) / 4` (the
`quality` metric) and reports the mean across dimensions as the overall figure. Reserve
5 for genuinely excellent work; 3 is competent-with-gaps.

## Dimensions

### 1. Evidence grounding
Are the claims tied to specific cited evidence, and does the evidence actually say what
the claim says it does?
- **1** - Assertions float free of evidence, or citations do not match the claim.
- **3** - Mostly grounded, with some hand-waving or loosely attached citations.
- **5** - Every material claim rests on cited evidence that clearly supports it.

### 2. Specificity
Concrete versus generic. Does the analysis commit to particulars, or could it describe
any company in the space?
- **1** - Vague boilerplate; no named targets, numbers, trials, or comparisons.
- **3** - A mix of specific detail and generic filler.
- **5** - Concrete throughout: named targets, quantities, specific trials and comparisons.

### 3. Calibration
Does the stated confidence match the strength and consistency of the evidence?
- **1** - Confident claims on thin evidence, or hedging on strong evidence.
- **3** - Roughly calibrated with noticeable lapses.
- **5** - Confidence tracks evidence strength consistently across the write-up.

### 4. Coverage of the key question
Does the analysis answer the question its module exists to answer, and the material
sub-questions under it?
- **1** - Misses the core question or ignores major drivers.
- **3** - Covers the main point but leaves real gaps.
- **5** - Addresses the key question and the material sub-questions.

### 5. Internal consistency
Do the claims agree with each other and with the summary?
- **1** - Self-contradictory: claims fight each other or the summary.
- **3** - Mostly consistent, with minor tension.
- **5** - Fully coherent; the summary follows from the claims.

## Known limitations

- **Uncalibrated judge.** No human-agreement study yet. Quality judgments are more
  subjective than a supported/unsupported call, so the judge-human gap is likely wider
  here, not narrower.
- **Draft dimensions.** The five dimensions and their weights (currently equal) are a
  starting point for analyst review, not a settled standard.
- **Self-grading risk.** If the judge model is the same one that produced the analysis,
  it may reward its own style. Run the judge as a different model where possible.
- **Not a truth check.** This grades craft against the rubric, not real-world accuracy.
  Pair it with faithfulness and retrieval evals, never use it alone.
