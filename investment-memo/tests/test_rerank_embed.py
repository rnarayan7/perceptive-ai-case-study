"""Tests for the local dense embedder (LSA) and the feature reranker.

Deterministic, no network, no model. numpy-only. These lock the behavior the retrieval
eval relies on: the embedder produces a semantic space where related text is nearer than
unrelated text, and the reranker promotes a lexically-on-topic chunk within the pool while
leaving the tail beyond the pool untouched.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from memo.rag.embeddings import LsaEmbedder
from memo.rag.rerank import FeatureReranker
from memo.rag.types import Chunk, RetrievalResult


def _cos(a, b):
    import numpy as _np
    a, b = _np.array(a), _np.array(b)
    na, nb = _np.linalg.norm(a), _np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def test_lsa_embedder_places_related_text_nearer():
    corpus = [
        "asthma airway inflammation wheezing respiratory disease",
        "asthma airway wheezing respiratory disease patients",
        "myasthenia autoimmune neuromuscular acetylcholine muscle weakness",
        "myasthenia autoimmune acetylcholine muscle weakness receptor",
    ]
    # min_df=1 keeps single-doc terms: on a tiny toy corpus almost nothing survives
    # min_df=2. The real corpus has hundreds of chunks where the default min_df=2 holds.
    emb = LsaEmbedder(dim=3, min_df=1).fit(corpus)
    assert emb.dim >= 1
    vecs = emb.embed(corpus)
    # An asthma query should sit closer to the asthma docs than to the myasthenia docs.
    q = emb.embed_query("wheezing airway disease asthma")
    asthma_sim = max(_cos(q, vecs[0]), _cos(q, vecs[1]))
    mg_sim = max(_cos(q, vecs[2]), _cos(q, vecs[3]))
    assert asthma_sim > mg_sim


def test_lsa_embedder_requires_fit():
    with pytest.raises(RuntimeError):
        LsaEmbedder().embed(["anything"])


def _result(doc_id, text, score, title=""):
    chunk = Chunk(
        chunk_id=f"{doc_id}::0", doc_id=doc_id, company="T", source="s",
        doc_type="d", title=title, url="u", text=text, position=0,
    )
    return RetrievalResult(chunk=chunk, score=score, retriever="bm25")


def test_reranker_promotes_on_topic_chunk_within_pool():
    # Two candidates with the SAME first-stage score; the reranker should prefer the one
    # that actually covers the query terms and matches the phrase.
    query = "kt-621 stat6 asthma"
    on_topic = _result("A", "KT-621 is an oral STAT6 degrader studied in asthma", 5.0)
    off_topic = _result("B", "unrelated boilerplate about corporate governance", 5.0)
    out = FeatureReranker().rerank(query, [off_topic, on_topic])
    assert out[0].chunk.doc_id == "A"
    assert out[0].retriever == "reranked"


def test_reranker_leaves_tail_beyond_pool_untouched():
    query = "asthma"
    head = [_result(f"h{i}", "asthma airway disease", 5.0 - i) for i in range(3)]
    tail = [_result(f"t{i}", "asthma airway disease", 1.0 - i) for i in range(2)]
    out = FeatureReranker(pool=3).rerank(query, head + tail)
    # The last two (tail) keep their identity and order.
    assert [r.chunk.doc_id for r in out[-2:]] == ["t0", "t1"]
