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
import math
from collections import Counter
from typing import Dict, List, Optional, Sequence


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


class LsaEmbedder(Embedder):
    """A local dense embedder: TF-IDF followed by truncated SVD (latent semantic analysis).

    Fit on the company's own chunk corpus, it needs no API key and no model download, only
    numpy. It captures term co-occurrence, so a query and a passage that mean the same thing
    with different words (the vocabulary-mismatch case BM25 misses) still land near each
    other. It is deliberately not a neural embedder; it is the lightweight, deterministic
    dense signal we fuse with BM25 to test whether a semantic lever helps hit@1.

    Uses the shared :func:`~memo.rag.lexical.tokenize` (drug-code aware) so the dense and
    lexical halves see the same tokens. Call :meth:`fit` before :meth:`embed`; the retriever
    does this at build time.
    """

    def __init__(self, dim: int = 200, max_vocab: int = 5000, min_df: int = 2) -> None:
        self.dim = dim
        self.max_vocab = max_vocab
        self.min_df = min_df
        self._vocab: Dict[str, int] = {}
        self._idf = None          # numpy array (V,)
        self._components = None   # numpy array (V, k): the SVD projection
        self._fitted = False

    def fit(self, texts: Sequence[str]) -> "LsaEmbedder":
        import numpy as np
        from memo.rag.lexical import tokenize

        docs = [tokenize(t) for t in texts]
        n = len(docs)
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))

        # Vocabulary: terms seen in at least min_df docs, capped to the max_vocab most
        # frequent. A too-rare term adds a noisy dimension; the cap bounds the SVD cost.
        terms = sorted((t for t, c in df.items() if c >= self.min_df),
                       key=lambda t: df[t], reverse=True)[:self.max_vocab]
        self._vocab = {t: i for i, t in enumerate(terms)}
        v = len(self._vocab)
        if v == 0 or n == 0:
            self._fitted = True
            self.dim = 0
            return self

        idf = np.zeros(v, dtype=np.float64)
        for t, i in self._vocab.items():
            idf[i] = math.log((1.0 + n) / (1.0 + df[t])) + 1.0
        self._idf = idf

        matrix = np.zeros((n, v), dtype=np.float64)
        for r, d in enumerate(docs):
            for term, count in Counter(d).items():
                j = self._vocab.get(term)
                if j is not None:
                    matrix[r, j] = count
        matrix *= idf[np.newaxis, :]
        _l2_normalize_rows(matrix, np)

        k = max(1, min(self.dim, v, n))
        # Truncated SVD: X = U S Vt; the right singular vectors Vt[:k] are the latent
        # topic axes. Projecting a tf-idf vector onto them gives its dense embedding.
        _u, _s, vt = np.linalg.svd(matrix, full_matrices=False)
        self._components = np.ascontiguousarray(vt[:k].T)  # (V, k)
        self.dim = int(k)
        self._fitted = True
        return self

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        import numpy as np

        if not self._fitted:
            raise RuntimeError("LsaEmbedder.fit(corpus) must be called before embed()")
        if self._components is None:  # degenerate corpus (empty vocab)
            return [[0.0] for _ in texts]
        out: List[List[float]] = []
        for text in texts:
            # errstate guards a spurious matmul RuntimeWarning from macOS Accelerate
            # (results are finite; nan_to_num is belt-and-suspenders).
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                vec = self._tfidf_vector(text, np) @ self._components  # (k,)
            vec = np.nan_to_num(vec)
            norm = float(np.linalg.norm(vec))
            if norm:
                vec = vec / norm
            out.append(vec.astype(float).tolist())
        return out

    # ---------------------------------------------------------------- internals

    def _tfidf_vector(self, text: str, np):
        from memo.rag.lexical import tokenize

        vec = np.zeros(len(self._vocab), dtype=np.float64)
        for term, count in Counter(tokenize(text)).items():
            j = self._vocab.get(term)
            if j is not None:
                vec[j] = count
        vec *= self._idf
        norm = float(np.linalg.norm(vec))
        if norm:
            vec /= norm
        return vec


def _l2_normalize_rows(matrix, np) -> None:
    """L2-normalize each row of a 2-D array in place (zero rows left as zero)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix /= norms
