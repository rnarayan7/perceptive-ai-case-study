"""The retriever the rest of the system calls.

Holds the chunk corpus and a BM25 index, applies metadata filters (company, source,
doc_type), and returns cited results. A dense ``Embedder`` is optional: when one is
supplied later, its hits are fused with BM25 via reciprocal-rank fusion. Today it runs
lexical-only, which is the deliberate v1 choice.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from memo.rag.embeddings import Embedder
from memo.rag.lexical import BM25Index
from memo.rag.types import Chunk, RetrievalResult
from memo.trace import NULL_TRACER, NullTracer, Tracer

# Reciprocal-rank-fusion constant; 60 is the value from the original RRF paper.
_RRF_K = 60


class Retriever:
    """Hybrid-capable retriever over a chunk corpus (lexical now, dense-ready)."""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        embedder: Optional[Embedder] = None,
        tracer: Tracer = NULL_TRACER,
        run_id: Optional[str] = None,
    ) -> None:
        self.chunks: List[Chunk] = list(chunks)
        self.embedder = embedder
        self.tracer = tracer
        self.run_id = run_id or ""
        self._bm25 = BM25Index().build([c.text for c in self.chunks])
        # Dense index would be built here from self.embedder when one is configured.

    def retrieve(
        self,
        query: str,
        k: int = 8,
        company: Optional[str] = None,
        source: Optional[str] = None,
        doc_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[RetrievalResult]:
        """Return up to ``k`` cited chunks for ``query``, honoring metadata filters.

        ``since`` / ``until`` are inclusive ISO date bounds (YYYY-MM-DD) that restrict the
        search to a time window, enabling retrieval across or within periods. A chunk whose
        document has no date is kept (a date filter only removes documents provably outside
        the window), so undated evidence is never silently dropped.
        """
        with self.tracer.span(
            self.run_id, "retrieval", "retrieve", query=query, k=k,
            filters={"company": company, "source": source, "doc_type": doc_type,
                     "since": since, "until": until},
        ) as event:
            candidates = self._filter(
                company=company, source=source, doc_type=doc_type, since=since, until=until
            )
            if not candidates:
                event["n_results"] = 0
                return []

            lexical = self._bm25.search(query, k=k, candidates=candidates)
            if self.embedder is None:
                results = [
                    RetrievalResult(chunk=self.chunks[i], score=score, retriever="bm25")
                    for i, score in lexical
                ]
            else:
                # Dense path (inactive until an Embedder is configured): fuse the rankings.
                dense = self._dense_search(query, k=k, candidates=candidates)
                results = self._fuse(lexical, dense, k=k)

            event["n_results"] = len(results)
            event["top_score"] = results[0].score if results else 0.0
            if not isinstance(self.tracer, NullTracer):
                # Full capture (opt-in when traced): the ranked hits with snippets, so a
                # retrieval is replayable, not just counted.
                event["results"] = [
                    {"doc_id": r.chunk.doc_id, "chunk_id": r.chunk.chunk_id,
                     "score": round(r.score, 3),
                     "snippet": " ".join(r.chunk.text.split())[:200]}
                    for r in results
                ]
            return results

    # ---------------------------------------------------------------- internals

    def _filter(
        self,
        company: Optional[str],
        source: Optional[str],
        doc_type: Optional[str],
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[int]:
        indices = []
        for i, chunk in enumerate(self.chunks):
            if company and chunk.company != company:
                continue
            if source and chunk.source != source:
                continue
            if doc_type and chunk.doc_type != doc_type:
                continue
            if not _within_dates(chunk.date, since, until):
                continue
            indices.append(i)
        return indices

    def _dense_search(self, query: str, k: int, candidates: Sequence[int]):
        raise NotImplementedError("dense retrieval is not configured; pass an Embedder")

    def _fuse(self, lexical, dense, k: int) -> List[RetrievalResult]:
        """Reciprocal-rank fusion of two (index, score) rankings."""
        scores: Dict[int, float] = {}
        for ranking in (lexical, dense):
            for rank, (idx, _score) in enumerate(ranking):
                scores[idx] = scores.get(idx, 0.0) + 1.0 / (_RRF_K + rank + 1)
        ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:k]
        return [
            RetrievalResult(chunk=self.chunks[i], score=score, retriever="fused")
            for i, score in ordered
        ]


def _within_dates(date: Optional[str], since: Optional[str], until: Optional[str]) -> bool:
    """Whether an ISO date falls within [since, until]. Undated chunks always pass."""
    if since is None and until is None:
        return True
    if not date:
        return True  # a date filter only excludes documents provably outside the window
    day = date[:10]  # normalize to YYYY-MM-DD; ISO strings compare lexicographically
    if since is not None and day < since[:10]:
        return False
    if until is not None and day > until[:10]:
        return False
    return True
