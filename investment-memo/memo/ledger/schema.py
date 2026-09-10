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
    value: Optional[str] = None  # the claim's value as written ("$0.4-0.9B", "35-50%")
    value_num: Optional[float] = None  # parsed numeric form of value, for the web app
    value_unit: Optional[str] = None  # unit of value_num ("USD", "%", ...)
    claim_id: str = field(default_factory=lambda: new_id("cl"))
    evidence: List[EvidenceRecord] = field(default_factory=list)


@dataclass
class SectionRecord:
    """A per-section rollup the web app reads for the conviction dots and takeaways.

    The analysis modules compute a module-level confidence and one-line summary; this row
    carries them per memo section so the dashboard does not have to re-derive them.
    """

    memo_id: str
    section_id: str
    module: str
    confidence: float
    tier: str = ""  # high | med | low, derived from confidence
    summary: str = ""


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


@dataclass
class FeedbackMessageRecord:
    """One turn in a feedback conversation about an analysis section."""

    role: str  # analyst | agent
    text: str
    ts: str = field(default_factory=_now)


@dataclass
class FeedbackSessionRecord:
    """One analyst feedback conversation flagging whether a section is accurate.

    Carries the section it targets, the optional highlighted quote being questioned, and a
    short structured summary (issue + severity) the agent emits so feedback is queryable
    over time, not just as raw transcripts. Turns live in ``messages`` (a child table).
    """

    company: str
    section_id: str
    section_label: str
    highlighted_quote: Optional[str] = None
    status: str = "open"  # open | captured
    issue: Optional[str] = None  # the specific inaccuracy the analyst raised
    severity: Optional[str] = None  # low | med | high
    session_id: str = field(default_factory=lambda: new_id("fb"))
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: List[FeedbackMessageRecord] = field(default_factory=list)


@dataclass
class GenerationRunRecord:
    """A run-level summary of one memo generation, for the Activity feed.

    Carries the cost/token totals and outcome so the Activity page can show what the
    autonomous system did without re-reading the execution trace. (Cost is surfaced for
    now to aid development; it becomes internal-only later.)
    """

    run_id: str  # == memo_id
    company: str
    status: str  # completed | refused | running
    trigger: str = "manual"  # manual | scheduled | data_change
    output: str = ""  # "N claims · M sections", or the refusal reason
    refusal_reason: Optional[str] = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    ran_at: str = field(default_factory=_now)
    memo_id: Optional[str] = None
