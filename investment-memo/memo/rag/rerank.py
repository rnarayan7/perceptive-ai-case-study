"""A deterministic feature reranker over a retrieved candidate pool.

The first-stage retriever (BM25, optionally fused with dense) is tuned for recall: it
almost always puts the right document somewhere in the top-k. The remaining error is
ranking, getting the right document to rank first (hit@1). A cross-encoder is the usual
second stage, but that needs a model we do not have here, so this reranker re-scores the
top pool with cheap, high-signal lexical features instead:

- coverage    - fraction of the query's distinct terms present in the chunk
- phrase      - an adjacent query bigram appears adjacently in the chunk (a real phrase
                match, not just scattered terms)
- code        - a drug/asset code in the query (e.g. KT-333) appears in the chunk; these
                are the most discriminating terms in this domain
- title       - query terms landing in the chunk's title/section heading
- base        - the first-stage score, normalized over the pool, kept as the anchor

Deterministic, no model, no key. It re-orders only the top ``pool`` candidates and leaves
the tail untouched, so it can only reshuffle near the head where hit@1/@3 are decided.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from memo.rag.lexical import _CODE, tokenize
from memo.rag.types import RetrievalResult

# Feature weights. The base (first-stage) score stays the largest single term so the
# reranker refines the recall-tuned order rather than overriding it; coverage is the main
# lever that pulls a lexically-on-topic chunk above an incidental one.
_W_BASE = 0.60
_W_COVERAGE = 0.18
_W_PHRASE = 0.10
_W_CODE = 0.08
_W_TITLE = 0.04


class FeatureReranker:
    """Re-score the top ``pool`` first-stage results with lexical relevance features."""

    def __init__(self, pool: int = 20) -> None:
        self.pool = pool

    def rerank(self, query: str, results: Sequence[RetrievalResult]) -> List[RetrievalResult]:
        head = list(results[: self.pool])
        tail = list(results[self.pool :])
        if not head:
            return list(results)

        q_tokens = tokenize(query)
        q_set = set(q_tokens)
        q_codes = {m.group().replace("-", "") for m in _CODE.finditer(query.lower())}
        max_base = max((r.score for r in head), default=1.0) or 1.0

        scored: List[Tuple[float, RetrievalResult]] = []
        for r in head:
            chunk_tokens = tokenize(r.chunk.text)
            chunk_set = set(chunk_tokens)
            title_set = set(tokenize(r.chunk.title or ""))

            coverage = (len(q_set & chunk_set) / len(q_set)) if q_set else 0.0
            phrase = 1.0 if _adjacent_bigram(q_tokens, chunk_tokens) else 0.0
            code = 1.0 if (q_codes & chunk_set) else 0.0
            title = (len(q_set & title_set) / len(q_set)) if q_set else 0.0
            base = r.score / max_base

            score = (
                _W_BASE * base
                + _W_COVERAGE * coverage
                + _W_PHRASE * phrase
                + _W_CODE * code
                + _W_TITLE * title
            )
            scored.append((score, r))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        reranked = [
            RetrievalResult(chunk=r.chunk, score=score, retriever="reranked")
            for score, r in scored
        ]
        return reranked + tail


def _adjacent_bigram(query_tokens: Sequence[str], chunk_tokens: Sequence[str]) -> bool:
    """True if any adjacent pair of query tokens appears adjacently in the chunk."""
    if len(query_tokens) < 2 or len(chunk_tokens) < 2:
        return False
    chunk_bigrams = set(zip(chunk_tokens, chunk_tokens[1:]))
    return any(pair in chunk_bigrams for pair in zip(query_tokens, query_tokens[1:]))
