"""Core data types for retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Chunk:
    """A retrievable span of a document, carrying its citation metadata.

    A chunk always knows which document, source, and position it came from, so a
    retrieval result can be cited back to a specific place without a second lookup.
    """

    chunk_id: str  # stable id, "<doc_id>::<position>"
    doc_id: str
    company: str
    source: str
    doc_type: str
    title: str
    url: str
    text: str
    position: int  # index of this chunk within its document
    metadata: Dict[str, Any] = field(default_factory=dict)
    date: Optional[str] = None  # document date (ISO, e.g. filing/first-posted date)

    @property
    def citation(self) -> str:
        """Short human-readable citation, e.g. 'edgar 10-Q (KYMR) <url>'."""
        return f"{self.source} {self.doc_type} ({self.company}) {self.url}".strip()


@dataclass
class RetrievalResult:
    """A chunk returned by the retriever with its relevance score and origin."""

    chunk: Chunk
    score: float
    retriever: str = ""  # which retriever produced it: "bm25", "dense", "fused"
