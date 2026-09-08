"""The dense-retrieval slot, deliberately left empty for v1.

We chose to ship lexical (BM25) plus structured lookup first and add semantic
embeddings only if evaluation shows lexical retrieval missing relevant passages
because of vocabulary mismatch. This module defines the interface a dense backend
will implement, so it drops into the retriever later without touching callers.

To add embeddings later: implement ``Embedder`` (e.g. an ``OpenAIEmbedder`` or a
local ``SentenceTransformerEmbedder``), pass it to ``Retriever(embedder=...)``, and
the retriever fuses dense hits with BM25 via reciprocal-rank fusion.
"""

from __future__ import annotations

import abc
from typing import List, Sequence


class Embedder(abc.ABC):
    """Interface for a text-embedding backend."""

    #: Dimensionality of the produced vectors. Set by concrete backends.
    dim: int = 0

    @abc.abstractmethod
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch of texts into vectors."""
        raise NotImplementedError

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query. Backends may override for asymmetric models."""
        return self.embed([text])[0]


# No concrete backend is registered yet. The retriever treats ``embedder=None`` as
# lexical-only, so nothing here needs a stub to keep the system runnable.
