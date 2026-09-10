"""Build a retriever from ingested data on disk."""

from __future__ import annotations

from typing import List, Optional

from memo.ingestion.base import Storage
from memo.rag.chunking import Chunker
from memo.rag.embeddings import Embedder, LsaEmbedder
from memo.rag.rerank import FeatureReranker
from memo.rag.retriever import Retriever
from memo.trace import NULL_TRACER, Tracer


def build_retriever(
    company: str,
    storage: Optional[Storage] = None,
    chunker: Optional[Chunker] = None,
    source: Optional[str] = None,
    embedder: Optional[Embedder] = None,
    dense: bool = False,
    rerank: bool = False,
    tracer: Tracer = NULL_TRACER,
    run_id: Optional[str] = None,
) -> Retriever:
    """Load a company's ingested documents, chunk them, and return a ready retriever.

    This is the single entry point for standing up retrieval over a company. Retrieval is
    lexical (BM25) by default. Set ``dense=True`` to fuse in a local LSA dense signal, and
    ``rerank=True`` to add the feature reranker; pass an explicit ``embedder`` to override
    the default dense backend. Pass a ``tracer`` + ``run_id`` to emit a per-query event.
    """
    storage = storage or Storage()
    chunker = chunker or Chunker()
    documents = storage.load_documents(company, source=source)
    chunks = chunker.chunk_documents(documents)
    if embedder is None and dense:
        embedder = LsaEmbedder()
    reranker = FeatureReranker() if rerank else None
    return Retriever(
        chunks, embedder=embedder, reranker=reranker, tracer=tracer, run_id=run_id
    )


def load_companies(storage: Optional[Storage] = None) -> List[str]:
    """List companies that have ingested data on disk."""
    storage = storage or Storage()
    root = storage.root
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())
