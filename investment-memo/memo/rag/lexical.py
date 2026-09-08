"""BM25 lexical retrieval, pure standard library.

BM25 is the lexical half of retrieval. It handles the exact terms that matter most
in this domain (drug names, NCT ids, endpoint names, financial line items) and needs
no model, key, or dependency. Implemented directly so a fresh clone runs it as-is.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

_TOKEN = re.compile(r"[a-z0-9]+")
# Drug/asset codes like "KT-333" or "IMVT-1402": letters, a hyphen, then digits.
# These are the most distinctive terms in the domain, so we keep the code intact as
# a single token instead of letting it split into a common prefix ("kt") plus a number.
_CODE = re.compile(r"[a-z]{1,6}-\d{2,}[a-z0-9]*")


def tokenize(text: str) -> List[str]:
    """Lowercase and tokenize.

    Alphanumeric runs become tokens (digits kept, so 'NCT04772885' survives). Hyphenated
    asset codes are additionally emitted de-hyphenated (e.g. 'KT-333' -> 'kt333'), so a
    rare drug code stays a rare, high-signal token rather than a common prefix + a number.
    """
    low = text.lower()
    tokens = _TOKEN.findall(low)
    for match in _CODE.finditer(low):
        tokens.append(match.group().replace("-", ""))
    return tokens


class BM25Index:
    """Okapi BM25 over a fixed set of documents (here, chunk texts).

    Build once with :meth:`build`, then :meth:`search`. ``search`` can be scoped to a
    subset of document indices, which is how the retriever applies metadata filters
    (company, source, doc_type) without maintaining a separate index per slice.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: List[List[str]] = []
        self._doc_len: List[int] = []
        self._tf: List[Counter] = []
        self._idf: Dict[str, float] = {}
        self._avgdl: float = 0.0

    @property
    def size(self) -> int:
        return len(self._docs)

    def build(self, texts: Sequence[str]) -> "BM25Index":
        self._docs = [tokenize(t) for t in texts]
        self._doc_len = [len(d) for d in self._docs]
        self._tf = [Counter(d) for d in self._docs]
        n = len(self._docs)
        self._avgdl = (sum(self._doc_len) / n) if n else 0.0

        df: Counter = Counter()
        for tokens in self._tf:
            df.update(tokens.keys())
        # BM25 idf with the standard +0.5 smoothing (floored at 0 to avoid negatives).
        self._idf = {
            term: max(0.0, math.log(1 + (n - freq + 0.5) / (freq + 0.5)))
            for term, freq in df.items()
        }
        return self

    def search(
        self,
        query: str,
        k: int = 10,
        candidates: Optional[Sequence[int]] = None,
    ) -> List[Tuple[int, float]]:
        """Return up to ``k`` (doc_index, score) pairs, highest score first.

        ``candidates`` restricts scoring to those document indices (used for filtering).
        Documents with a zero score are dropped.
        """
        query_terms = tokenize(query)
        if not query_terms or not self._docs:
            return []

        indices = range(len(self._docs)) if candidates is None else candidates
        scored: List[Tuple[int, float]] = []
        for i in indices:
            score = self._score(query_terms, i)
            if score > 0:
                scored.append((i, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def _score(self, query_terms: Sequence[str], doc_index: int) -> float:
        tf = self._tf[doc_index]
        dl = self._doc_len[doc_index]
        denom_norm = self.k1 * (1 - self.b + self.b * (dl / self._avgdl if self._avgdl else 0.0))
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = self._idf.get(term, 0.0)
            score += idf * (freq * (self.k1 + 1)) / (freq + denom_norm)
        return score
