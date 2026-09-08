"""Retrieval over ingested company data.

v1 is lexical (BM25) plus structured field access; a dense embedding backend slots
in behind :class:`~memo.rag.embeddings.Embedder` when evaluation shows it is needed.
"""

from memo.rag.chunking import Chunker
from memo.rag.embeddings import Embedder
from memo.rag.index import build_retriever, load_companies
from memo.rag.lexical import BM25Index, tokenize
from memo.rag.retriever import Retriever
from memo.rag.structured import FilingRecord, PriceRecord, StructuredStore, TrialRecord
from memo.rag.types import Chunk, RetrievalResult

__all__ = [
    "Chunk",
    "RetrievalResult",
    "Chunker",
    "BM25Index",
    "tokenize",
    "Retriever",
    "build_retriever",
    "load_companies",
    "Embedder",
    "StructuredStore",
    "TrialRecord",
    "FilingRecord",
    "PriceRecord",
]
