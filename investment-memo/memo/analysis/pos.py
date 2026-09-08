"""Probability-of-success (PoS) analysis.

Answers "how likely is this program to succeed" as a calibrated probability band,
built bottom-up from the retrieved scientific evidence rather than from memorized
phase-transition base rates. The estimate is a documented rubric: each factor
(mechanism plausibility, trial design, readout strength, safety, regulatory
precedent, CMC risk) is scored as its own atomic, cited claim, and the overall band
is reasoned from those factor claims.

Design note on grounding: identical to MoA. The module retrieves evidence, labels
each chunk E1..En, and the model may cite only those labels. We resolve the labels
back to real chunks, so a claim can never carry a citation the model invented. Any
label the model cites that we did not provide is dropped and recorded in notes.

Design note on base rates: industry-average success rates (e.g. "Phase 2 oncology
transitions ~30%") are deliberately forbidden. They are memorized priors, not
evidence about *this* program, and would make the memo unfaithful. The band must
follow from the cited factor evidence; thin evidence widens the band and lowers
confidence rather than borrowing precision from a remembered average.
"""

from __future__ import annotations

from typing import Dict, List

from memo.analysis.base import AnalysisModule, AnalysisResult, Claim, Evidence
from memo.analysis.context import AnalysisContext
from memo.rag.types import Chunk

# The six rubric factors. Each maps to one atomic factor claim; the model tags every
# claim with one of these keys so a reader can see which risk it speaks to.
_FACTORS = {
    "mechanism": "mechanism plausibility / target validation",
    "design": "trial design quality (randomization, control, endpoint, powering)",
    "readout": "readout strength (effect size, significance, consistency)",
    "safety": "safety and tolerability profile",
    "regulatory": "regulatory precedent for the indication",
    "cmc": "CMC / manufacturing risk",
}

_SYSTEM = (
    "You are a biotech equity analyst estimating a drug program's probability of "
    "clinical and regulatory success (PoS). "
    "Work only from the numbered evidence provided. Every claim must cite one or more "
    "evidence ids (E1, E2, ...) that directly support it. Do not use outside knowledge "
    "to assert facts, and do not introduce specific names, numbers, or comparisons that "
    "are not present in the cited evidence. "
    "CRITICAL: do NOT use memorized industry base rates or phase-transition success "
    "averages (e.g. 'Phase 2 programs succeed ~30% of the time'). Those are priors, "
    "not evidence about this program, and are forbidden. Build the estimate bottom-up "
    "from the cited evidence only. "
    "Score the program against a fixed rubric of six factors: "
    "(mechanism) mechanism plausibility / target validation; "
    "(design) trial design quality (randomization, control, endpoint appropriateness, "
    "powering); "
    "(readout) readout strength (effect size, statistical significance, consistency); "
    "(safety) safety and tolerability profile; "
    "(regulatory) regulatory precedent for the indication; "
    "(cmc) CMC / manufacturing risk. "
    "Emit one atomic claim per factor for which the evidence says anything, tagging each "
    "with its factor key. Make each claim a single, atomic assertion: one fact per claim. "
    "Do not bundle several facts into one claim; split them. "
    "Then give one overall PoS as a probability BAND (e.g. '35-50%'), with a rationale "
    "that ties the band to the factor claims. If the evidence is thin or a factor is "
    "unaddressed, widen the band and lower confidence rather than inventing precision."
)

# PoS needs the clinical and regulatory picture; each query pulls a different slice.
_QUERIES = [
    "trial design randomization control primary endpoint powering",
    "efficacy readout effect size statistical significance response rate",
    "safety adverse events tolerability discontinuation",
    "clinical trial phase enrollment patient numbers",
    "regulatory precedent approval indication FDA pathway",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_pos_band": {"type": "string"},
        "overall_confidence": {"type": "number"},
        "summary": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                    "factor": {
                        "type": "string",
                        "enum": list(_FACTORS.keys()),
                    },
                },
                "required": [
                    "statement",
                    "evidence_ids",
                    "confidence",
                    "rationale",
                    "factor",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overall_pos_band", "overall_confidence", "summary", "claims"],
    "additionalProperties": False,
}


class ProbabilityOfSuccessModule(AnalysisModule):
    name = "pos"

    def __init__(self, k_per_query: int = 6, max_evidence: int = 14) -> None:
        self.k_per_query = k_per_query
        self.max_evidence = max_evidence

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        labeled = self._gather_evidence(context)
        if not labeled:
            return AnalysisResult(
                company=context.company, module=self.name,
                summary="", confidence=0.0,
                notes=["no evidence retrieved; cannot assess probability of success"],
            )

        user = self._build_prompt(context.company, labeled)
        response = context.model.complete_json(_SYSTEM, user, _SCHEMA)
        return self._to_result(context.company, response, labeled)

    # ---------------------------------------------------------------- internals

    def _gather_evidence(self, context: AnalysisContext) -> Dict[str, Chunk]:
        """Retrieve across the PoS queries and label a deduped chunk set E1..En."""
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
            "Task: score the program against the six rubric factors, one atomic claim "
            "per factor the evidence addresses, each citing its evidence ids, a factor "
            "tag, and a calibrated confidence. Then give one overall probability-of-"
            "success band and a rationale tying it to those factor claims. Use only the "
            "cited evidence; do not fall back on industry base rates.",
        ]
        return "\n".join(lines)

    def _to_result(self, company, response, labeled: Dict[str, Chunk]) -> AnalysisResult:
        data = response.data
        notes: List[str] = []
        claims: List[Claim] = []

        # The overall PoS claim goes first so the band leads the memo. It aggregates the
        # evidence of the factor claims, so it is grounded in the same sources it rests on.
        band = str(data.get("overall_pos_band", "") or "")
        overall_conf = float(data.get("overall_confidence", 0.0))
        summary = data.get("summary", "")

        factor_claims: List[Claim] = []
        for raw in data.get("claims", []):
            evidence, missing = self._resolve_evidence(raw.get("evidence_ids", []), labeled)
            if missing:
                notes.append(f"claim cited unknown evidence ids {missing}; dropped those")
            if not evidence:
                notes.append(
                    f"ungrounded claim (no valid evidence): {raw.get('statement', '')[:80]}"
                )
            factor_key = raw.get("factor", "")
            factor_label = _FACTORS.get(factor_key, factor_key)
            rationale = raw.get("rationale", "")
            if factor_label:
                rationale = f"[{factor_label}] {rationale}"
            factor_claims.append(
                Claim(
                    statement=raw.get("statement", ""),
                    confidence=float(raw.get("confidence", 0.0)),
                    rationale=rationale,
                    evidence=evidence,
                )
            )

        overall_evidence = self._dedupe_evidence(factor_claims)
        if not band:
            notes.append("model returned no overall PoS band")
        if not overall_evidence:
            notes.append("overall PoS band rests on no grounded factor claim")
        overall_claim = Claim(
            statement=f"Overall probability of success: {band}" if band
            else "Overall probability of success",
            confidence=overall_conf,
            rationale=summary,
            evidence=overall_evidence,
            value=band or None,
        )

        claims.append(overall_claim)
        claims.extend(factor_claims)

        return AnalysisResult(
            company=company,
            module=self.name,
            summary=summary,
            claims=claims,
            confidence=overall_conf,
            notes=notes,
            usage=response.usage,
        )

    @staticmethod
    def _dedupe_evidence(claims: List[Claim]) -> List[Evidence]:
        """Union the evidence across factor claims, keeping each chunk once."""
        seen: set = set()
        merged: List[Evidence] = []
        for claim in claims:
            for ev in claim.evidence:
                key = ev.chunk_id or (ev.doc_id, ev.quote)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(ev)
        return merged

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
