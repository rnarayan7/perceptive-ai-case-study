"""Turn documents into retrievable chunks.

Word-based chunking with overlap, packed along paragraph boundaries so a chunk
rarely splits mid-sentence. Structure-aware chunking (tables, figures kept as
typed units) is a later upgrade; this is the prose-oriented v1.
"""

from __future__ import annotations

import re
from typing import List

from memo.ingestion.base import Document
from memo.rag.types import Chunk

# EDGAR inline-XBRL primary documents begin with a block of machine tags before the
# readable prose. This heuristic trims a leading run of that noise so it does not
# dominate the first chunk. It is intentionally conservative.
_XBRL_HINT = re.compile(r"(us-gaap:|dei:|xbrli:|iso4217:|\bContext\b)")


class Chunker:
    """Splits a :class:`Document` into overlapping word-windowed :class:`Chunk` objects."""

    def __init__(self, chunk_words: int = 300, overlap_words: int = 50) -> None:
        if overlap_words >= chunk_words:
            raise ValueError("overlap_words must be smaller than chunk_words")
        self.chunk_words = chunk_words
        self.overlap_words = overlap_words

    def chunk_document(self, document: Document) -> List[Chunk]:
        text = self._preclean(document)
        if not text.strip():
            return []

        windows = self._windows(text)
        chunks: List[Chunk] = []
        for position, window in enumerate(windows):
            chunks.append(
                Chunk(
                    chunk_id=f"{document.doc_id}::{position}",
                    doc_id=document.doc_id,
                    company=document.company,
                    source=document.source,
                    doc_type=document.doc_type,
                    title=document.title,
                    url=document.url,
                    text=window,
                    position=position,
                    metadata=dict(document.metadata),
                    date=document.published,
                )
            )
        return chunks

    def chunk_documents(self, documents: List[Document]) -> List[Chunk]:
        chunks: List[Chunk] = []
        for document in documents:
            chunks.extend(self.chunk_document(document))
        return chunks

    # ---------------------------------------------------------------- internals

    def _preclean(self, document: Document) -> str:
        """Light source-aware cleanup before chunking."""
        text = document.text
        if document.source == "edgar":
            text = self._trim_xbrl_preamble(text)
        return text

    @staticmethod
    def _trim_xbrl_preamble(text: str, scan_lines: int = 200) -> str:
        """Drop a leading block of XBRL tag lines if present, keeping the prose."""
        lines = text.splitlines()
        cut = 0
        for i, line in enumerate(lines[:scan_lines]):
            if _XBRL_HINT.search(line):
                cut = i + 1
        return "\n".join(lines[cut:]) if cut else text

    def _windows(self, text: str) -> List[str]:
        """Pack paragraphs into overlapping word windows."""
        words = text.split()
        if len(words) <= self.chunk_words:
            return [" ".join(words)] if words else []

        step = self.chunk_words - self.overlap_words
        windows: List[str] = []
        for start in range(0, len(words), step):
            window = words[start : start + self.chunk_words]
            if not window:
                break
            windows.append(" ".join(window))
            if start + self.chunk_words >= len(words):
                break
        return windows
