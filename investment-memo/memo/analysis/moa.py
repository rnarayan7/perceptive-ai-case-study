"""Mechanism-of-action analysis.

Answers "does the drug work" descriptively: the target and mechanism, and the clinical
proof points that support it. Confidence scales with the strength and consistency of
clinical evidence, not the elegance of the biology.

Design note on grounding: the module retrieves evidence, labels each chunk E1..En, and
the model may cite only those labels. We resolve the labels back to real chunks, so a
claim can never carry a citation the model invented. Any label the model cites that we
did not provide is dropped and recorded in notes.
"""

from __future__ import annotations

from typing import Dict, List

from memo.analysis.base import AnalysisModule, AnalysisResult, Claim, Evidence
from memo.analysis.context import AnalysisContext
from memo.rag.types import Chunk

_SYSTEM = (
    "You are a biotech equity analyst assessing a drug's mechanism of action. "
    "Work only from the numbered evidence provided. Every claim must cite one or more "
    "evidence ids (E1, E2, ...) that directly support it. Do not use outside knowledge "
    "to assert facts, and do not introduce specific names, numbers, or comparisons that "
    "are not present in the cited evidence. "
    "Make each claim a single, atomic assertion: one fact per claim. Do not bundle "
    "several facts into one claim. If a statement needs two sources for two facts, split "
    "it into two claims. If the evidence is thin, make fewer claims and lower confidence. "
    "Confidence reflects the strength and consistency of clinical evidence, not "
    "mechanistic plausibility alone."
)

# MoA needs mechanism and clinical proof; each query pulls a different slice.
_QUERIES = [
    "mechanism of action target pathway",
    "protein degradation degrader inhibitor target engagement",
    "pharmacodynamic biomarker reduction dose response clinical activity",
    "safety and clinical activity in patients",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "mechanism_summary": {"type": "string"},
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
                },
                "required": ["statement", "evidence_ids", "confidence", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["mechanism_summary", "overall_confidence", "claims"],
    "additionalProperties": False,
}


class MechanismModule(AnalysisModule):
    name = "moa"

    def __init__(self, k_per_query: int = 6, max_evidence: int = 14) -> None:
        self.k_per_query = k_per_query
        self.max_evidence = max_evidence

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        labeled = self._gather_evidence(context)
        if not labeled:
            return AnalysisResult(
                company=context.company, module=self.name,
                summary="", confidence=0.0,
                notes=["no evidence retrieved; cannot assess mechanism"],
            )

        user = self._build_prompt(context.company, labeled)
        response = context.model.complete_json(_SYSTEM, user, _SCHEMA)
        return self._to_result(context.company, response, labeled)

    # ---------------------------------------------------------------- internals

    def _gather_evidence(self, context: AnalysisContext) -> Dict[str, Chunk]:
        """Retrieve across the MoA queries and label a deduped chunk set E1..En."""
        seen: Dict[str, Chunk] = {}  # chunk_id -> chunk
        for query in _QUERIES:
            for result in context.retriever.retrieve(
                query, k=self.k_per_query, company=context.company
            ):
                seen.setdefault(result.chunk.chunk_id, result.chunk)
        chunks = list(seen.values())[: self.max_evidence]
        return {f"E{i + 1}": chunk for i, chunk in enumerate(chunks)}

    def _build_prompt(self, company: str, labeled: Dict[str, Chunk]) -> str:
        lines = [f"Company: {company}", "", "Evidence:"]
        for label, chunk in labeled.items():
            snippet = " ".join(chunk.text.split())[:1500]
            head = f"[{chunk.source} {chunk.doc_type}"
            if chunk.date:
                head += f", {chunk.date}"
            head += f", {chunk.url}]"
            lines.append(f"{label}: {head} {snippet}")
        lines += [
            "",
            "Task: summarize the mechanism of action and list the specific claims the "
            "evidence supports, each citing its evidence ids and a calibrated confidence.",
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
                notes.append(f"ungrounded claim (no valid evidence): {raw.get('statement','')[:80]}")
            claims.append(
                Claim(
                    statement=raw.get("statement", ""),
                    confidence=float(raw.get("confidence", 0.0)),
                    rationale=raw.get("rationale", ""),
                    evidence=evidence,
                )
            )

        return AnalysisResult(
            company=company,
            module=self.name,
            summary=data.get("mechanism_summary", ""),
            claims=claims,
            confidence=float(data.get("overall_confidence", 0.0)),
            notes=notes,
            usage=response.usage,
        )

    @staticmethod
    def _resolve_evidence(evidence_ids, labeled: Dict[str, Chunk]):
        """Map cited labels back to real chunks; report any that were not provided."""
        evidence: List[Evidence] = []
        missing: List[str] = []
        for label in evidence_ids:
            chunk = labeled.get(label)
            if chunk is None:
                missing.append(label)
                continue
            evidence.append(
                Evidence(
                    doc_id=chunk.doc_id,
                    source=chunk.source,
                    doc_type=chunk.doc_type,
                    url=chunk.url,
                    # Full cited text (capped generously) so a downstream judge grades
                    # against what the claim actually rests on, not a 300-char sliver.
                    quote=" ".join(chunk.text.split())[:2000],
                    date=chunk.date,
                    chunk_id=chunk.chunk_id,
                )
            )
        return evidence, missing
