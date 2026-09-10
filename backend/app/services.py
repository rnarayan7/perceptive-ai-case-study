"""Read-side aggregation over the ledger.

Turns the raw ledger rows (memos, claims, memo_sections, generation_runs) into the
shapes the screens need. No model calls, no writes; pure reads. Market price / market cap
come from the Alpha Vantage adapter (spec §7.4), cached on disk; when no key is
configured the adapter degrades to a stub and these stay null.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from memo.compose.engine import load_artifact
from memo.ingestion.base import Storage
from memo.ledger import LedgerStore

from app import deps
from app.marketdata import build_provider
from app.registry import COMPANIES, ORDER

# One provider for the process, rooted at the data dir so the price-snapshot
# cache lives alongside the ledger (spec §7.4).
_MARKET = build_provider(deps.DATA_ROOT)


def _market(ticker: str) -> Dict[str, Optional[float]]:
    """Market snapshot for a ticker, None-safe (never raises)."""
    try:
        snap = _MARKET.get(ticker)
    except Exception:
        snap = None
    snap = snap or {}
    return {
        "market_price": snap.get("market_price"),
        "market_cap": snap.get("market_cap"),
        "shares": snap.get("shares"),
    }


def _upside_pct(fair_value_usd: Optional[float], market_cap: Optional[float]) -> Optional[float]:
    """rNPV total vs market cap: fair_value / market_cap - 1 (None-safe)."""
    if fair_value_usd is None or not market_cap:
        return None
    return fair_value_usd / market_cap - 1


def _fair_value_per_sh(fair_value_usd: Optional[float], shares: Optional[float]) -> Optional[float]:
    if fair_value_usd is None or not shares:
        return None
    return fair_value_usd / shares

# The four dashboard questions map to these analysis sections.
_QUESTION_BY_SECTION = {
    "moa": "efficacy",
    "pos": "approval",
    "regulatory": "regulatory",
    "peak_sales": "market",
}


_LONG_WORDS = {"buy", "long", "overweight", "constructive", "accumulate", "bullish"}
_SHORT_WORDS = {"sell", "short", "underweight", "avoid", "bearish"}
_NEUTRAL_WORDS = {"hold", "neutral", "wait", "pass"}


def _thesis_direction(recommendation: Optional[str]) -> Optional[str]:
    """Long/short/neutral from the recommendation's leading stance word.

    The memo emits a free-text recommendation, not a structured direction, so classify on
    the stance it opens with. Whole-text scanning misfires (e.g. "Hold ... risk/reward
    turns constructive" reads as long), so the leading token wins; a bounded fallback only.
    """
    rec = (recommendation or "").strip().lower()
    if not rec:
        return None
    lead = "".join(ch if ch.isalpha() else " " for ch in rec).split()
    first = lead[0] if lead else ""
    if first in _NEUTRAL_WORDS:
        return "neutral"
    if first in _LONG_WORDS:
        return "long"
    if first in _SHORT_WORDS:
        return "short"
    head = rec[:60]
    if any(w in head for w in _LONG_WORDS):
        return "long"
    if any(w in head for w in _SHORT_WORDS):
        return "short"
    return "neutral"


def _rnpv_usd(claims) -> Optional[float]:
    for c in claims:
        if c.module == "valuation" and c.value_num:
            return c.value_num
    return None


def _variant_view(memo_id: str) -> Optional[str]:
    """The differentiated view for a memo, read from its rendered artifact (None-safe).

    Carried on the artifact rather than the ledger to avoid a schema migration; a missing
    artifact or field yields None.
    """
    try:
        return load_artifact(memo_id, Storage(root=deps.DATA_ROOT)).get("variant_view")
    except Exception:
        return None


def _peak_usd(claims) -> Optional[float]:
    for c in claims:
        if c.section == "peak_sales" and c.value_unit == "USD" and c.value_num:
            return c.value_num
    return None


def _label(ev) -> str:
    """A short, distinguishing citation-chip label (source + doc id)."""
    src = (getattr(ev, "source", "") or "").strip()
    if src == "clinicaltrials":
        return ev.doc_id or "clinicaltrials"
    if ev.doc_id:
        return f"{src} {ev.doc_id}".strip() if src else ev.doc_id
    dt = (ev.doc_type or "").strip()
    return (f"{src} {dt}".strip()) or src or "source"


def _section_findings(sec_claims) -> Dict[str, Any]:
    """Top findings (bullets) + the source set for a section (one chip per document)."""
    ranked = sorted(sec_claims, key=lambda c: c.confidence, reverse=True)
    points = [c.statement for c in ranked[:3] if c.statement]
    seen: set = set()
    cits: List[Dict[str, Any]] = []
    for c in ranked:
        for ev in c.evidence:
            key = ev.doc_id or ev.evidence_id  # one chip per source document
            if not key or key in seen:
                continue
            seen.add(key)
            cits.append({"evidence_id": ev.evidence_id, "label": _label(ev), "url": ev.url})
    return {"points": points, "citations": cits[:5]}


def _conviction(sections) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for s in sections:
        q = _QUESTION_BY_SECTION.get(s.section_id)
        if q:
            out[q] = s.tier
    return out


def company_summary(ledger: LedgerStore, ticker: str) -> Dict[str, Any]:
    """One coverage-dashboard row."""
    meta = COMPANIES[ticker]
    memo = ledger.latest_memo(ticker)
    mkt = _market(ticker)
    row: Dict[str, Any] = {
        "ticker": ticker, "name": meta["name"], "lead_asset": meta["lead_asset"],
        "indication": meta["indication"], "phase": meta["phase"],
        "status": "none", "memo_id": None, "thesis": None, "conviction": {},
        "fair_value_usd": None, "fair_value_per_sh": None, "peak_sales_usd": None,
        "market_price": mkt["market_price"], "market_cap": mkt["market_cap"],
        "shares": mkt["shares"], "upside_pct": None,
        "recommendation": None, "updated_at": None,
    }
    if memo is None:
        return row
    claims = ledger.get_claims(memo.memo_id)
    fair_value_usd = _rnpv_usd(claims)
    row.update(
        status=memo.status, memo_id=memo.memo_id,
        thesis=_thesis_direction(memo.recommendation),
        conviction=_conviction(ledger.get_sections(memo.memo_id)),
        fair_value_usd=fair_value_usd,
        fair_value_per_sh=_fair_value_per_sh(fair_value_usd, mkt["shares"]),
        peak_sales_usd=_peak_usd(claims),
        upside_pct=_upside_pct(fair_value_usd, mkt["market_cap"]),
        recommendation=memo.recommendation, updated_at=memo.created_at,
    )
    return row


def coverage(ledger: LedgerStore) -> List[Dict[str, Any]]:
    return [company_summary(ledger, t) for t in ORDER]


def company_detail(ledger: LedgerStore, ticker: str) -> Optional[Dict[str, Any]]:
    meta = COMPANIES.get(ticker)
    if meta is None:
        return None
    memo = ledger.latest_memo(ticker)
    if memo is None:
        return {"ticker": ticker, **meta, "memo_id": None, "status": "none",
                "metrics": {}, "kpis": {}, "recommendation": None, "thesis": None,
                "variant_view": None}
    claims = ledger.get_claims(memo.memo_id)
    by_section: Dict[str, List[Any]] = {}
    for c in claims:
        by_section.setdefault(c.section, []).append(c)
    metrics: Dict[str, Any] = {}
    for s in ledger.get_sections(memo.memo_id):
        q = _QUESTION_BY_SECTION.get(s.section_id)
        if not q:
            continue
        metrics[q] = {
            "confidence": s.tier,
            "takeaway": s.summary,
            **_section_findings(by_section.get(s.section_id, [])),
        }
    mkt = _market(ticker)
    fair_value_usd = _rnpv_usd(claims)
    return {
        "ticker": ticker, "name": meta["name"], "lead_asset": meta["lead_asset"],
        "indication": meta["indication"], "phase": meta["phase"],
        "status": memo.status, "memo_id": memo.memo_id,
        "thesis": _thesis_direction(memo.recommendation),
        "recommendation": memo.recommendation,
        "variant_view": _variant_view(memo.memo_id),
        "metrics": metrics,
        "kpis": {
            "fair_value_usd": fair_value_usd,
            "fair_value_per_sh": _fair_value_per_sh(fair_value_usd, mkt["shares"]),
            "peak_sales_usd": _peak_usd(claims),
            "market_price": mkt["market_price"], "market_cap": mkt["market_cap"],
            "shares": mkt["shares"],
            "upside_pct": _upside_pct(fair_value_usd, mkt["market_cap"]),
            "cash_usd": None, "runway": None,
        },
    }


def evidence_audit(ledger: LedgerStore, evidence_id: str) -> Optional[Dict[str, Any]]:
    ev = ledger.get_evidence_by_id(evidence_id)
    if ev is None:
        return None
    claim = ledger.get_claim_for_evidence(evidence_id)
    return {
        "evidence": asdict(ev),
        "claim": {
            "claim_id": claim.claim_id, "section": claim.section, "module": claim.module,
            "statement": claim.statement, "confidence": claim.confidence,
            "value": claim.value, "value_num": claim.value_num, "value_unit": claim.value_unit,
            "rationale": claim.rationale, "memo_id": claim.memo_id,
        } if claim else None,
    }


def generations(ledger: LedgerStore, limit: int = 25) -> List[Dict[str, Any]]:
    return [asdict(r) for r in ledger.list_generation_runs(limit=limit)]
