"""Persisted record shapes for the ledger.

These are the ledger's own row types, kept independent of the in-memory analysis
dataclasses (``memo.analysis.Claim`` / ``Evidence``) so the ledger has no dependency on
the analysis layer. The orchestrator (a later wave) maps analysis output into these rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class EvidenceRecord:
    """One citation backing a claim."""

    doc_id: str
    source: str
    doc_type: str
    url: str
    quote: str
    date: Optional[str] = None
    chunk_id: Optional[str] = None
    evidence_id: str = field(default_factory=lambda: new_id("ev"))


@dataclass
class ClaimRecord:
    """One grounded assertion in a memo, with its evidence."""

    memo_id: str
    section: str  # which memo section this claim belongs to
    module: str  # producing analysis module (moa, pos, ...)
    statement: str
    confidence: float
    rationale: str = ""
    value: Optional[str] = None
    claim_id: str = field(default_factory=lambda: new_id("cl"))
    evidence: List[EvidenceRecord] = field(default_factory=list)


@dataclass
class MemoRecord:
    """One memo run for one company."""

    memo_id: str  # also the run_id, correlating with the execution trace
    company: str
    status: str = "draft"  # draft | complete | refused
    recommendation: Optional[str] = None
    thesis: Optional[str] = None
    notes: str = ""
    created_at: str = field(default_factory=_now)
