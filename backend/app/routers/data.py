"""Data (documents) endpoints (spec §5.2). Indexes the ingested corpus + cited-by counts.

Figures are not built yet, so there is no figures endpoint here; the frontend's Figures
toggle is a disabled "coming soon" state.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_ledger
from app import documents

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/documents")
def list_documents(
    company: Optional[str] = None,
    doc_type: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    ledger=Depends(get_ledger),
):
    return documents.list_documents(
        ledger, company=company, doc_type=doc_type, q=q,
        date_from=date_from, date_to=date_to,
    )


@router.get("/documents/{doc_id}")
def document(doc_id: str, ledger=Depends(get_ledger)):
    detail = documents.document_detail(ledger, doc_id)
    if detail is None:
        raise HTTPException(404, f"unknown document {doc_id}")
    return detail
