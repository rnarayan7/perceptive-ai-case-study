"""Shared grounding helpers for analysis modules.

Every module grounds the same way: retrieve evidence, label it E1..En, let the model
cite only those labels, then resolve labels back to real chunks so a citation can never
be fabricated. These helpers centralize that so each module only defines its own
queries, prompt, and output schema.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from memo.analysis.base import Evidence
from memo.analysis.context import AnalysisContext
from memo.rag.types import Chunk


def gather_labeled_evidence(
    context: AnalysisContext,
    queries: Sequence[str],
    k_per_query: int = 6,
    max_evidence: int = 14,
) -> Dict[str, Chunk]:
    """Retrieve across ``queries`` and return a deduped, labeled chunk set {E1: chunk}."""
    seen: Dict[str, Chunk] = {}
    for query in queries:
        for result in context.retriever.retrieve(
            query, k=k_per_query, company=context.company
        ):
            seen.setdefault(result.chunk.chunk_id, result.chunk)
    chunks = list(seen.values())[:max_evidence]
    return {f"E{i + 1}": chunk for i, chunk in enumerate(chunks)}


def format_evidence_block(labeled: Dict[str, Chunk], snippet_chars: int = 1500) -> str:
    """Render labeled evidence for a prompt: 'E1: [source doc_type, date, url] text'."""
    lines: List[str] = ["Evidence:"]
    for label, chunk in labeled.items():
        snippet = " ".join(chunk.text.split())[:snippet_chars]
        head = f"[{chunk.source} {chunk.doc_type}"
        if chunk.date:
            head += f", {chunk.date}"
        head += f", {chunk.url}]"
        lines.append(f"{label}: {head} {snippet}")
    return "\n".join(lines)


def resolve_evidence(
    evidence_ids: Sequence[str],
    labeled: Dict[str, Chunk],
    quote_chars: int = 2000,
) -> Tuple[List[Evidence], List[str]]:
    """Map cited labels back to real chunks; return (evidence, unknown_labels).

    ``quote`` keeps the full cited text (capped generously) so a downstream faithfulness
    judge grades against what the claim actually rests on, not a sliver.
    """
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
                quote=" ".join(chunk.text.split())[:quote_chars],
                date=chunk.date,
                chunk_id=chunk.chunk_id,
            )
        )
    return evidence, missing
