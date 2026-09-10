"""Activity endpoints (spec §5.4). v1 surfaces generation runs with cost/summary."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_ledger
from app import ingestion_activity, services

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("/generations")
def generations(limit: int = 25, ledger=Depends(get_ledger)):
    return services.generations(ledger, limit=limit)


@router.get("/ingestion")
def ingestion():
    """Per-source ingestion summary for the Activity page (no ledger needed)."""
    return ingestion_activity.ingestion_summary()
