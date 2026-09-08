"""Analytical-quality evaluation.

Grades an analysis module's output for analytical QUALITY, not just faithfulness.
Faithfulness asks "is each claim supported by its evidence"; this asks the harder,
softer question "is this good analysis" against a documented rubric: is it grounded,
specific, well-calibrated, does it cover the key question, and is it internally
consistent. A judge model scores each rubric dimension 1-5 with a short justification.

The rubric lives in ``evals/rubrics/analysis_quality.md`` and is a DRAFT pending analyst
sign-off. Same honesty caveats as faithfulness apply, and then some:

- **Uncalibrated.** ``calibrated=False`` in params. Until the judge's 1-5 scores are
  validated against human analyst scores on the same outputs, treat them as directional
  only. Rubric quality judgments are more subjective than a supported/unsupported call,
  so the gap between judge and human is likely wider here, not narrower.
- **Draft rubric.** The dimensions and anchors have not been signed off by an analyst.
  Both the rubric and the judge need human validation before these scores gate anything.
- **Self-grading risk.** If the judge is the same model that wrote the analysis, it may
  reward its own style. The judge model is configurable so it can differ from the
  generator.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from memo.analysis.base import AnalysisResult
from memo.analysis.model import ModelClient
from memo.eval.base import Evaluator
from memo.eval.types import CaseResult, EvalReport, mean

# The scored rubric dimensions. Names and anchors are documented in
# evals/rubrics/analysis_quality.md; keep the two in sync.
_DIMENSIONS: Tuple[str, ...] = (
    "evidence_grounding",
    "specificity",
    "calibration",
    "coverage",
    "internal_consistency",
)

_MIN_SCORE = 1
_MAX_SCORE = 5

_SYSTEM = (
    "You are a senior biotech equity analyst grading the analytical quality of a "
    "junior analyst's write-up. You are NOT fact-checking against the outside world; "
    "you are judging craft against a fixed rubric. Score each dimension on an integer "
    "1-5 scale using these anchors:\n"
    "- evidence_grounding: are the claims tied to specific cited evidence? "
    "1 = assertions float free of evidence; 3 = mostly grounded, some hand-waving; "
    "5 = every material claim rests on and matches its cited evidence.\n"
    "- specificity: concrete vs generic. 1 = vague boilerplate that could describe any "
    "company; 3 = a mix of specific and generic; 5 = concrete named targets, numbers, "
    "trials, and comparisons.\n"
    "- calibration: does stated confidence match evidence strength? 1 = confident claims "
    "on thin evidence, or hedging on strong evidence; 3 = roughly calibrated with lapses; "
    "5 = confidence tracks the strength and consistency of the evidence throughout.\n"
    "- coverage: does the analysis actually answer its module's key question? "
    "1 = misses the core question or major drivers; 3 = covers the main point, gaps "
    "remain; 5 = addresses the key question and the material sub-questions.\n"
    "- internal_consistency: do the claims and summary agree with each other? "
    "1 = self-contradictory; 3 = mostly consistent, minor tension; 5 = fully coherent, "
    "the summary follows from the claims.\n"
    "Be strict and specific: reserve 5 for genuinely excellent work and justify every "
    "score in one or two sentences pointing at what you saw."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": list(_DIMENSIONS)},
                    # NB: structured-output JSON schema does not support integer
                    # minimum/maximum; the 1-5 band is enforced by the prompt and by
                    # _clamp_score() on the response, not by the schema.
                    "score": {"type": "integer"},
                    "justification": {"type": "string"},
                },
                "required": ["name", "score", "justification"],
                "additionalProperties": False,
            },
        },
        "overall_comment": {"type": "string"},
    },
    "required": ["dimensions", "overall_comment"],
    "additionalProperties": False,
}


def _clamp_score(score: int) -> int:
    """Coerce a judge score into the valid 1-5 band."""
    try:
        value = int(score)
    except (TypeError, ValueError):
        return _MIN_SCORE
    return max(_MIN_SCORE, min(_MAX_SCORE, value))


def dimension_metrics(score: int) -> Dict[str, float]:
    """Pure mapping from a 1-5 dimension score to metric values (testable without a model).

    ``score`` is the raw 1-5 rubric score; ``quality`` is that score normalized to 0-1
    so it aggregates cleanly and reads on the same scale as the other evaluators.
    """
    clamped = _clamp_score(score)
    return {
        "score": float(clamped),
        "quality": (clamped - _MIN_SCORE) / (_MAX_SCORE - _MIN_SCORE),
    }


class QualityEvaluator(Evaluator):
    name = "quality"

    def __init__(self, analysis: AnalysisResult, judge: ModelClient) -> None:
        self.analysis = analysis
        self.judge = judge

    def run(self) -> EvalReport:
        scores, comment, usage = self._judge()

        case_results: List[CaseResult] = [
            CaseResult(
                case_id=dimension,
                query=dimension,
                relevant=[],
                retrieved=[],
                metrics=dimension_metrics(score),
                detail={"justification": justification},
            )
            for dimension, (score, justification) in scores.items()
        ]

        return EvalReport(
            name=self.name,
            company=self.analysis.company,
            params={
                "module": self.analysis.module,
                "dimensions": len(case_results),
                "rubric": "evals/rubrics/analysis_quality.md (DRAFT)",
                "overall_comment": comment,
                "judge_input_tokens": usage.get("input_tokens", 0),
                "judge_output_tokens": usage.get("output_tokens", 0),
                "calibrated": False,
            },
            case_results=case_results,
            aggregate=self._aggregate(case_results),
        )

    # ---------------------------------------------------------------- internals

    def _judge(self) -> Tuple[Dict[str, Tuple[int, str]], str, Dict[str, int]]:
        response = self.judge.complete_json(_SYSTEM, self._build_prompt(), _SCHEMA)
        data = response.data

        scores: Dict[str, Tuple[int, str]] = {}
        for item in data.get("dimensions", []):
            name = item.get("name")
            if name in _DIMENSIONS and name not in scores:
                scores[name] = (
                    _clamp_score(item.get("score", _MIN_SCORE)),
                    item.get("justification", ""),
                )

        # Any dimension the judge omitted scores the floor, so a partial response cannot
        # silently inflate the aggregate by dropping the weak dimensions.
        for dimension in _DIMENSIONS:
            scores.setdefault(dimension, (_MIN_SCORE, "not scored by judge"))

        # Preserve rubric order regardless of the order the judge returned.
        ordered = {d: scores[d] for d in _DIMENSIONS}
        return ordered, data.get("overall_comment", ""), response.usage

    def _build_prompt(self) -> str:
        analysis = self.analysis
        lines = [
            f"Company: {analysis.company}",
            f"Analysis module: {analysis.module}",
            "",
            "SUMMARY:",
            analysis.summary or "(no summary)",
            "",
            f"CLAIMS ({len(analysis.claims)}):",
        ]
        if not analysis.claims:
            lines.append("(no claims produced)")
        for i, claim in enumerate(analysis.claims, start=1):
            lines.append(f"{i}. [confidence {claim.confidence:.2f}] {claim.statement}")
            if claim.value:
                lines.append(f"   estimate: {claim.value}")
            if claim.rationale:
                lines.append(f"   rationale: {claim.rationale}")
            if claim.evidence:
                for e in claim.evidence:
                    lines.append(f"   evidence [{e.source} {e.doc_type}]: {e.quote}")
            else:
                lines.append("   evidence: (none cited)")
        lines += [
            "",
            "Task: score the analytical quality of the write-up above on each rubric "
            "dimension (1-5) with a short justification, then give one overall comment. "
            "Judge the craft against the rubric, not against outside facts.",
        ]
        return "\n".join(lines)

    def _aggregate(self, case_results: List[CaseResult]) -> Dict[str, float]:
        if not case_results:
            return {}
        keys = case_results[0].metrics.keys()
        return {k: mean([c.metrics[k] for c in case_results]) for k in keys}
