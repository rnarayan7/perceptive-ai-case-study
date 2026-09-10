"""Read-side index over the ingested document corpus (spec §5.2 / §7.3).

Walks the on-disk corpus (data/<company>/<source>/<doc_id>.json) via the memo package's
``Storage`` and turns it into the flat rows the Data library needs. ``figure_count`` is
the number of annotated figures on disk for that document's COMPANY (see
``_figure_counts``): the figures pipeline links figures to a company/asset, not to a single
source document, so the count is company-level, not per-document.

Reading all 755 docs per request is acceptable for v1; the loaded corpus is cached in a
module-level variable so repeated list/detail requests don't re-walk the disk.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from memo.ingestion import REGISTRY
from memo.ingestion.base import Document, Storage

from app.deps import DATA_ROOT
from app.registry import ORDER

# Cache the loaded corpus once. It's static on disk for v1 (ingestion runs offline), so
# there's no invalidation path; restart the process to pick up a re-ingest.
_CACHE: Optional[List[Document]] = None
_LOCK = threading.Lock()

# Company -> count of annotated figures on disk. Linkage is company-level (the figures
# pipeline annotates figures per company/asset, with no clean per-document mapping), so
# every document of a company reports its company's figure count.
_FIGURE_COUNTS: Optional[Dict[str, int]] = None


def _figure_counts() -> Dict[str, int]:
    """annotated PNGs per company, from data/<company>/figures/annotated/."""
    global _FIGURE_COUNTS
    if _FIGURE_COUNTS is None:
        with _LOCK:
            if _FIGURE_COUNTS is None:
                counts: Dict[str, int] = {}
                for company in ORDER:
                    annotated = DATA_ROOT / company / "figures" / "annotated"
                    if annotated.is_dir():
                        counts[company] = sum(
                            1
                            for p in annotated.iterdir()
                            if p.is_file() and p.suffix.lower() == ".png"
                        )
                _FIGURE_COUNTS = counts
    return _FIGURE_COUNTS


def _figure_count_for(company: str) -> int:
    return _figure_counts().get(company.upper(), 0)


def _load_corpus() -> List[Document]:
    """Every ingested Document across the coverage universe × all sources."""
    storage = Storage(root=DATA_ROOT)
    docs: List[Document] = []
    for company in ORDER:
        for source in REGISTRY:
            docs.extend(storage.load_documents(company, source))
    return docs


def _corpus() -> List[Document]:
    global _CACHE
    if _CACHE is None:
        with _LOCK:
            if _CACHE is None:
                _CACHE = _load_corpus()
    return _CACHE


def _row(doc: Document, cited_by_count: int) -> Dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "source": doc.source,
        "doc_type": doc.doc_type,
        "company": doc.company,
        "published": doc.published,
        "figure_count": _figure_count_for(doc.company),  # company-level (no per-doc linkage)
        "cited_by_count": cited_by_count,
    }


def list_documents(
    ledger,
    company: Optional[str] = None,
    doc_type: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filtered, newest-first list of documents for the Data library."""
    company_f = company.upper() if company else None
    q_f = q.lower().strip() if q else None

    rows: List[Dict[str, Any]] = []
    for doc in _corpus():
        if company_f and doc.company.upper() != company_f:
            continue
        if doc_type and doc.doc_type != doc_type:
            continue
        if q_f and q_f not in (doc.title or "").lower():
            continue
        if date_from or date_to:
            if not doc.published:
                continue
            if date_from and doc.published < date_from:
                continue
            if date_to and doc.published > date_to:
                continue
        rows.append(_row(doc, ledger.count_evidence_by_doc(doc.doc_id)))

    # Newest first; documents with no published date sort last.
    rows.sort(key=lambda r: r["published"] or "", reverse=True)
    return rows


def document_detail(ledger, doc_id: str) -> Optional[Dict[str, Any]]:
    """One document (incl. full text) plus the memos/sections that cite it."""
    doc = next((d for d in _corpus() if d.doc_id == doc_id), None)
    if doc is None:
        return None
    return {
        "document": {
            "doc_id": doc.doc_id,
            "company": doc.company,
            "source": doc.source,
            "doc_type": doc.doc_type,
            "title": doc.title,
            "url": doc.url,
            "published": doc.published,
            "retrieved_at": doc.retrieved_at,
            "metadata": doc.metadata,
            "text": doc.text,
            "figure_count": _figure_count_for(doc.company),  # company-level (no per-doc linkage)
        },
        "cited_by": ledger.cited_by(doc_id),
    }
