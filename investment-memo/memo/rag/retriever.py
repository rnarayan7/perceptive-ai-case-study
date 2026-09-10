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
from memo.rag.rerank import FeatureReranker
from memo.rag.types import Chunk, RetrievalResult
from memo.trace import NULL_TRACER, NullTracer, Tracer

# Reciprocal-rank-fusion constant; 60 is the value from the original RRF paper.
_RRF_K = 60


class Retriever:
    """Hybrid-capable retriever over a chunk corpus (lexical, optional dense + rerank)."""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        embedder: Optional[Embedder] = None,
        reranker: Optional[FeatureReranker] = None,
        tracer: Tracer = NULL_TRACER,
        run_id: Optional[str] = None,
    ) -> None:
        self.chunks: List[Chunk] = list(chunks)
        self.embedder = embedder
        self.reranker = reranker
        self.tracer = tracer
        self.run_id = run_id or ""
        self._bm25 = BM25Index().build([c.text for c in self.chunks])
        # Dense index: fit the embedder on this corpus (if it fits) and precompute a
        # normalized doc-vector matrix so retrieval is one matmul. Lexical-only when absent.
        self._doc_vecs = None
        if self.embedder is not None and self.chunks:
            import numpy as np

            if hasattr(self.embedder, "fit") and not getattr(self.embedder, "_fitted", False):
                self.embedder.fit([c.text for c in self.chunks])
            self._doc_vecs = np.asarray(
                self.embedder.embed([c.text for c in self.chunks]), dtype=np.float64
            )

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

            # When reranking, fetch a larger pool from the first stage so the reranker has
            # real candidates to reorder within the head where hit@1/@3 are decided.
            fetch_k = max(k, self.reranker.pool) if self.reranker is not None else k

            lexical = self._bm25.search(query, k=fetch_k, candidates=candidates)
            if self.embedder is None or self._doc_vecs is None:
                results = [
                    RetrievalResult(chunk=self.chunks[i], score=score, retriever="bm25")
                    for i, score in lexical
                ]
            else:
                # Dense path: fuse the lexical and dense rankings via reciprocal-rank fusion.
                dense = self._dense_search(query, k=fetch_k, candidates=candidates)
                results = self._fuse(lexical, dense, k=fetch_k)

            if self.reranker is not None:
                results = self.reranker.rerank(query, results)
            results = results[:k]

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
        """Cosine-similarity search over the precomputed doc vectors, scoped to candidates.

        Returns up to ``k`` ``(chunk_index, similarity)`` pairs, highest first. Vectors are
        L2-normalized at embed time, so a dot product is cosine similarity.
        """
        import numpy as np

        q = np.asarray(self.embedder.embed_query(query), dtype=np.float64)
        if q.shape[0] != self._doc_vecs.shape[1]:
            return []
        idx = np.fromiter(candidates, dtype=np.int64)
        # errstate guards a spurious matmul RuntimeWarning from macOS Accelerate; the
        # similarities are finite (nan_to_num defends against any genuine NaN).
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            sims = np.nan_to_num(self._doc_vecs[idx] @ q)
        order = np.argsort(sims)[::-1][:k]
        return [(int(idx[o]), float(sims[o])) for o in order if sims[o] > 0]

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
