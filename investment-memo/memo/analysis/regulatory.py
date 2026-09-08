"""Regulatory-path analysis.

Answers "how does this drug reach approval": its development phase, any regulatory
designations (fast track, breakthrough, orphan, PRIME), whether the endpoint supports
accelerated or full approval, the planned filing and its timeline, and the likelihood of
an advisory committee. Confidence scales with how directly the evidence speaks to each
regulatory fact, not with how favorable the path looks.

Honesty over completeness: a regulatory memo is only useful if it never invents a filing
date, a designation, or a PDUFA that no public source states. Where the evidence is
silent on a fact, the module reports it as *not disclosed* rather than guessing. A
"not disclosed" finding is itself a valid claim: it can cite the closest evidence, or
carry no evidence and be recorded as a note.

Design note on grounding: the module retrieves evidence, labels each chunk E1..En, and
the model may cite only those labels. We resolve the labels back to real chunks, so a
claim can never carry a citation the model invented. Any label the model cites that we
did not provide is dropped and recorded in notes.
"""

from __future__ import annotations

from typing import Dict, List

from memo.analysis import grounding
from memo.analysis.base import AnalysisModule, AnalysisResult, Claim
from memo.analysis.context import AnalysisContext
from memo.rag.types import Chunk

_SYSTEM = (
    "You are a biotech equity analyst assessing a drug's regulatory path to approval. "
    "Work only from the numbered evidence provided. Every claim must cite one or more "
    "evidence ids (E1, E2, ...) that directly support it. Do not use outside knowledge "
    "to assert facts, and do not introduce specific names, dates, numbers, designations, "
    "or endpoints that are not present in the cited evidence. "
    "Make each claim a single, atomic assertion: one fact per claim. Do not bundle "
    "several facts into one claim. If a statement needs two sources for two facts, split "
    "it into two claims. "
    "CRITICAL honesty rule: if the evidence does not contain a fact, say the fact is not "
    "disclosed. Never invent a filing date, a PDUFA date, a regulatory designation, an "
    "endpoint, or an approval pathway that the evidence does not state. Treating a fact "
    "as not disclosed is correct and useful: make it an explicit claim (statement such as "
    "'No PDUFA date is disclosed in the available evidence'). Such a claim may cite the "
    "closest relevant evidence, or none at all if nothing bears on it. "
    "Cover, where the evidence supports it: development phase; any regulatory "
    "designations (fast track, breakthrough, orphan, PRIME); whether the primary endpoint "
    "supports accelerated versus full approval; the planned NDA/BLA filing and its "
    "timeline or PDUFA; and the likelihood of an FDA advisory committee. For each area "
    "the evidence does not address, add a not-disclosed claim rather than omitting it. "
    "Confidence reflects how directly the evidence supports the claim, not how favorable "
    "the regulatory path appears."
)

# Each query pulls a different slice of the regulatory picture.
_QUERIES = [
    "fast track breakthrough therapy orphan drug PRIME designation",
    "accelerated approval versus full approval primary endpoint acceptability surrogate",
    "registrational pivotal Phase 3 trial",
    "planned NDA BLA filing submission PDUFA date timeline",
    "FDA advisory committee AdCom",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "overall_confidence": {"type": "number"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                    "aspect": {"type": "string"},
                },
                "required": [
                    "statement",
                    "evidence_ids",
                    "confidence",
                    "rationale",
                    "aspect",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "overall_confidence", "claims"],
    "additionalProperties": False,
}


class RegulatoryModule(AnalysisModule):
    name = "regulatory"

    def __init__(self, k_per_query: int = 6, max_evidence: int = 14) -> None:
        self.k_per_query = k_per_query
        self.max_evidence = max_evidence

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        labeled = self._gather_evidence(context)
        if not labeled:
            return AnalysisResult(
                company=context.company, module=self.name,
                summary="", confidence=0.0,
                notes=["no evidence retrieved; cannot assess regulatory path"],
            )

        user = self._build_prompt(context.company, labeled)
        response = context.model.complete_json(_SYSTEM, user, _SCHEMA)
        return self._to_result(context.company, response, labeled)

    # ---------------------------------------------------------------- internals

    def _gather_evidence(self, context: AnalysisContext) -> Dict[str, Chunk]:
        """Retrieve across the regulatory queries and label a deduped chunk set E1..En."""
        return grounding.gather_labeled_evidence(
            context,
            _QUERIES,
            k_per_query=self.k_per_query,
            max_evidence=self.max_evidence,
        )

    def _build_prompt(self, company: str, labeled: Dict[str, Chunk]) -> str:
        lines = [f"Company: {company}", ""]
        lines.append(grounding.format_evidence_block(labeled))
        lines += [
            "",
            "Task: describe the regulatory path to approval and list the specific claims "
            "the evidence supports, each an atomic assertion citing its evidence ids, a "
            "calibrated confidence, and an aspect (e.g. 'phase', 'designation', "
            "'endpoint', 'filing'). Cover development phase, regulatory designations, "
            "endpoint acceptability (accelerated vs full approval), planned filing and "
            "timeline, and advisory-committee likelihood. Where the evidence does not "
            "disclose a fact, state that it is not disclosed rather than guessing.",
        ]
        return "\n".join(lines)

    def _to_result(self, company, response, labeled: Dict[str, Chunk]) -> AnalysisResult:
        data = response.data
        notes: List[str] = []
        claims: List[Claim] = []

        for raw in data.get("claims", []):
            evidence, missing = self._resolve_evidence(raw.get("evidence_ids", []), labeled)
            if missing:
                notes.append(f"claim cited unknown evidence ids {missing}; dropped those")
            if not evidence:
                notes.append(
                    f"ungrounded claim (no valid evidence): {raw.get('statement', '')[:80]}"
                )
            rationale = raw.get("rationale", "")
            aspect = raw.get("aspect", "")
            if aspect:
                rationale = f"[{aspect}] {rationale}" if rationale else f"[{aspect}]"
            claims.append(
                Claim(
                    statement=raw.get("statement", ""),
                    confidence=float(raw.get("confidence", 0.0)),
                    rationale=rationale,
                    evidence=evidence,
                )
            )

        return AnalysisResult(
            company=company,
            module=self.name,
            summary=data.get("summary", ""),
            claims=claims,
            confidence=float(data.get("overall_confidence", 0.0)),
            notes=notes,
            usage=response.usage,
        )

    @staticmethod
    def _resolve_evidence(evidence_ids, labeled: Dict[str, Chunk]):
        """Map cited labels back to real chunks; report any that were not provided."""
        return grounding.resolve_evidence(evidence_ids, labeled)
