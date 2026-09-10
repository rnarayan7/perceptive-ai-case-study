"""Companies + audit endpoints (spec §5.1). Reads the ledger + rendered-memo artifacts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from memo.compose.engine import load_artifact

from app.deps import get_ledger, get_storage
from app import services

router = APIRouter(prefix="/api", tags=["companies"])


@router.get("/companies")
def list_companies(ledger=Depends(get_ledger)):
    return services.coverage(ledger)


@router.get("/companies/{ticker}")
def company(ticker: str, ledger=Depends(get_ledger)):
    detail = services.company_detail(ledger, ticker.upper())
    if detail is None:
        raise HTTPException(404, f"unknown company {ticker}")
    return detail


@router.get("/companies/{ticker}/memo")
def company_memo(ticker: str, ledger=Depends(get_ledger), storage=Depends(get_storage)):
    memo = ledger.latest_memo(ticker.upper())
    if memo is None:
        raise HTTPException(404, f"no memo for {ticker}")
    try:
        return load_artifact(memo.memo_id, storage)
    except FileNotFoundError:
        raise HTTPException(404, "rendered memo artifact missing")


@router.get("/memos/{memo_id}")
def memo(memo_id: str, storage=Depends(get_storage)):
    try:
        return load_artifact(memo_id, storage)
    except FileNotFoundError:
        raise HTTPException(404, f"no memo {memo_id}")


@router.get("/evidence/{evidence_id}")
def evidence(evidence_id: str, ledger=Depends(get_ledger)):
    audit = services.evidence_audit(ledger, evidence_id)
    if audit is None:
        raise HTTPException(404, f"no evidence {evidence_id}")
    return audit
