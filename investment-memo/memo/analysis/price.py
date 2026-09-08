"""Price-vs-thesis analysis.

Frames what the market is being asked to price in and where the candidate's view
departs from it. The honest constraint here is corpus scope: the filings give us the
balance sheet (cash, shares outstanding, debt, and therefore runway), but they do NOT
contain a live share price, a market cap, or street/consensus estimates. This module
extracts the balance-sheet facts that ARE grounded, and refuses to invent the two that
are not: it records the live price/market cap and consensus as explicit MISSING INPUTS,
and frames any price-vs-thesis reasoning as conditional on a price supplied elsewhere.

Design note on grounding: like the other modules, this one retrieves evidence, labels
each chunk E1..En, and lets the model cite only those labels. Labels are resolved back
to real chunks, so a claim can never carry a citation the model invented. Deterministic
arithmetic (e.g. net cash = cash - debt) is done in code, not by the model.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from memo.analysis import grounding
from memo.analysis.base import AnalysisModule, AnalysisResult, Claim
from memo.analysis.context import AnalysisContext
from memo.rag.types import Chunk

_SYSTEM = (
    "You are a biotech equity analyst laying out what the market is being asked to "
    "price in for a company, working only from the numbered evidence provided. "
    "Extract the balance-sheet facts that appear in the evidence: cash and cash "
    "equivalents, shares outstanding, debt (convertible notes / notes payable), and "
    "any stated cash runway. Every claim must cite one or more evidence ids "
    "(E1, E2, ...) that directly support it. "
    "Make each claim a single, atomic assertion: one fact per claim. Do not bundle "
    "several facts into one claim. Do not use outside knowledge, and do not introduce "
    "numbers, names, or comparisons that are not present in the cited evidence. "
    "CRITICAL: you do NOT have the live share price, the market capitalization, or any "
    "street/consensus estimate. These are not in the evidence. Never fabricate them. "
    "Where such a required input is absent, say so explicitly in missing_inputs and "
    "keep the price-vs-thesis framing conditional on a price supplied elsewhere "
    "(for example: enterprise value cannot be computed without a market price; given "
    "cash and debt, EV = market cap - cash + debt). "
    "If the evidence is thin, make fewer claims and lower confidence."
)

# The balance-sheet slices that anchor a price-vs-thesis read. Each query targets one.
_QUERIES = [
    "cash and cash equivalents marketable securities",
    "shares outstanding common stock",
    "total operating expenses net loss cash runway operations funded",
    "convertible debt notes payable long-term debt",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "overall_confidence": {"type": "number"},
        "missing_inputs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                    "metric": {"type": "string"},
                },
                "required": [
                    "statement",
                    "evidence_ids",
                    "confidence",
                    "rationale",
                    "metric",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "overall_confidence", "missing_inputs", "claims"],
    "additionalProperties": False,
}

# Inputs a price-vs-thesis read needs but that our filings corpus does not contain.
# Surfaced as notes so the memo is explicit about what it could not ground.
_CORPUS_GAPS = [
    "live share price / market capitalization: no public source in corpus",
    "consensus / street estimates: no public source in corpus",
]


def net_cash(cash: float, debt: float) -> float:
    """Net cash = cash and equivalents minus total debt. Pure, deterministic arithmetic.

    Kept in code (not the model) so the number is exact and testable. Debt is treated
    as a positive magnitude; a company with more debt than cash returns a negative value.
    """
    return float(cash) - float(debt)


class PriceVsThesisModule(AnalysisModule):
    name = "price"

    def __init__(self, k_per_query: int = 6, max_evidence: int = 14) -> None:
        self.k_per_query = k_per_query
        self.max_evidence = max_evidence

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        labeled = grounding.gather_labeled_evidence(
            context,
            _QUERIES,
            k_per_query=self.k_per_query,
            max_evidence=self.max_evidence,
        )
        if not labeled:
            return AnalysisResult(
                company=context.company,
                module=self.name,
                summary="",
                confidence=0.0,
                # Even with no evidence, the corpus gaps still hold and are worth stating.
                notes=["no evidence retrieved; cannot extract balance-sheet facts"]
                + list(_CORPUS_GAPS),
            )

        user = self._build_prompt(context.company, labeled)
        response = context.model.complete_json(_SYSTEM, user, _SCHEMA)
        return self._to_result(context.company, response, labeled)

    # ---------------------------------------------------------------- internals

    def _build_prompt(self, company: str, labeled: Dict[str, Chunk]) -> str:
        lines = [
            f"Company: {company}",
            "",
            grounding.format_evidence_block(labeled),
            "",
            "Task: extract the balance-sheet facts the evidence supports (cash and "
            "equivalents, shares outstanding, debt, cash runway), one atomic claim each "
            "with its evidence ids, a metric label (one of: cash, shares, debt, "
            "runway), and a calibrated confidence. Then list in missing_inputs every "
            "input a price-vs-thesis read needs that the evidence does not contain -- in "
            "particular the live share price / market cap and consensus estimates -- and "
            "keep the summary's price framing conditional on a price supplied elsewhere.",
        ]
        return "\n".join(lines)

    def _to_result(
        self,
        company: str,
        response,
        labeled: Dict[str, Chunk],
    ) -> AnalysisResult:
        data = response.data
        notes: List[str] = []
        claims: List[Claim] = []

        for raw in data.get("claims", []):
            evidence, missing = grounding.resolve_evidence(
                raw.get("evidence_ids", []), labeled
            )
            if missing:
                notes.append(f"claim cited unknown evidence ids {missing}; dropped those")
            if not evidence:
                notes.append(
                    f"ungrounded claim (no valid evidence): {raw.get('statement', '')[:80]}"
                )
            metric = raw.get("metric", "")
            rationale = raw.get("rationale", "")
            if metric:
                rationale = f"[{metric}] {rationale}" if rationale else f"[{metric}]"
            claims.append(
                Claim(
                    statement=raw.get("statement", ""),
                    confidence=float(raw.get("confidence", 0.0)),
                    rationale=rationale,
                    evidence=evidence,
                )
            )

        # Fold the model's declared missing inputs into notes, and always append the
        # two structural corpus gaps (price, consensus) so the memo never silently
        # implies they were available. Dedupe while preserving order.
        notes.extend(_dedupe(data.get("missing_inputs", []) + list(_CORPUS_GAPS)))

        return AnalysisResult(
            company=company,
            module=self.name,
            summary=data.get("summary", ""),
            claims=claims,
            confidence=float(data.get("overall_confidence", 0.0)),
            notes=notes,
            usage=response.usage,
        )


def _dedupe(items: List[str]) -> List[str]:
    """Order-preserving de-duplication for note lines."""
    seen: set = set()
    out: List[str] = []
    for item in items:
        text = str(item)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out
