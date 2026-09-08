"""Faithfulness evaluation.

Grades whether each generated claim is supported by the evidence it cites, and only
that evidence. This is the hallucination/grounding check: distinct from retrieval (did
we find the right doc) and from real-world truth (is it actually true). A judge model
returns one of supported / partial / unsupported / contradicted per claim.

Two honesty caveats baked into how this is reported:
- **Uncalibrated.** Until the judge is validated against human labels, treat these as
  directional. The judge itself is a model and can be wrong.
- **Self-grading risk.** If the judge model is the same one that wrote the claims, it may
  agree with itself. The judge model is configurable so it can differ from the generator;
  the real fix (human calibration) is tracked in follow-ups.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from memo.analysis.base import AnalysisResult, Claim
from memo.analysis.model import ModelClient
from memo.eval.base import Evaluator
from memo.eval.types import CaseResult, EvalReport, mean

_VERDICTS = ("supported", "partial", "unsupported", "contradicted")

_SYSTEM = (
    "You are a meticulous fact-checker. Decide whether the CLAIM is supported by the "
    "EVIDENCE provided, using only that evidence and no outside knowledge. Verdicts: "
    "'supported' (the evidence directly establishes the claim), 'partial' (it supports a "
    "weaker version or only part), 'unsupported' (the evidence does not establish it), "
    "'contradicted' (the evidence contradicts it). Be strict: plausible is not supported."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(_VERDICTS)},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "rationale"],
    "additionalProperties": False,
}


def verdict_metrics(verdict: str) -> Dict[str, float]:
    """Pure mapping from a verdict to metric values (testable without a model)."""
    return {
        "supported": 1.0 if verdict == "supported" else 0.0,
        "contradicted": 1.0 if verdict == "contradicted" else 0.0,
        # headline score: full credit for supported, half for partial, none otherwise
        "faithfulness": 1.0 if verdict == "supported" else (0.5 if verdict == "partial" else 0.0),
    }


class FaithfulnessEvaluator(Evaluator):
    name = "faithfulness"

    def __init__(self, analysis: AnalysisResult, judge: ModelClient) -> None:
        self.analysis = analysis
        self.judge = judge

    def run(self) -> EvalReport:
        case_results: List[CaseResult] = []
        input_tokens = output_tokens = 0

        for i, claim in enumerate(self.analysis.claims, start=1):
            verdict, rationale, usage = self._judge_claim(claim)
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            case_results.append(
                CaseResult(
                    case_id=f"claim-{i}",
                    query=claim.statement,
                    relevant=[],
                    retrieved=[e.doc_id for e in claim.evidence],
                    metrics=verdict_metrics(verdict),
                    detail={"verdict": verdict, "rationale": rationale},
                )
            )

        return EvalReport(
            name=self.name,
            company=self.analysis.company,
            params={
                "module": self.analysis.module,
                "claims": len(case_results),
                "judge_input_tokens": input_tokens,
                "judge_output_tokens": output_tokens,
                "calibrated": False,
            },
            case_results=case_results,
            aggregate=self._aggregate(case_results),
        )

    # ---------------------------------------------------------------- internals

    def _judge_claim(self, claim: Claim) -> Tuple[str, str, Dict[str, int]]:
        # A claim with no evidence is unsupported by construction; no call needed.
        if not claim.evidence:
            return "unsupported", "no evidence cited", {}

        evidence_block = "\n".join(
            f"{i}. {e.quote}" for i, e in enumerate(claim.evidence, start=1)
        )
        user = f"CLAIM: {claim.statement}\n\nEVIDENCE:\n{evidence_block}\n\nGive your verdict."
        response = self.judge.complete_json(_SYSTEM, user, _SCHEMA, max_tokens=2000)
        data = response.data
        verdict = data.get("verdict", "unsupported")
        if verdict not in _VERDICTS:
            verdict = "unsupported"
        return verdict, data.get("rationale", ""), response.usage

    def _aggregate(self, case_results: List[CaseResult]) -> Dict[str, float]:
        if not case_results:
            return {}
        keys = case_results[0].metrics.keys()
        return {k: mean([c.metrics[k] for c in case_results]) for k in keys}
