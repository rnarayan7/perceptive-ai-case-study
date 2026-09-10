"""The memo composition engine.

``compose_memo`` is the top-level orchestrator: it runs every analysis module, maps
their claims into ledger rows and persists them, drafts each section's prose from those
grounded claims, synthesizes the cross-module thesis, folds in the deterministic rNPV
valuation and any figures, and writes the rendered-memo JSON the web app consumes.

Grounding discipline carries through the whole pipeline. Sections are drafted only from
claims that already carry evidence; the drafter is told to cite the evidence markers it
is given and invent nothing. A required section that ends up with no grounded claim is
not fabricated: the memo is marked ``refused`` and the failed section is named.

Optional dependencies (the valuation model and the figures package) are built by other
waves and imported defensively; when one is absent the memo degrades gracefully and says
what it could not compute rather than failing the run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from memo.analysis import REGISTRY, AnalysisContext, AnalysisResult
from memo.analysis.base import Claim
from memo.ingestion.base import Storage
from memo.ledger import (
    ClaimRecord,
    EvidenceRecord,
    GenerationRunRecord,
    LedgerStore,
    MemoRecord,
    SectionRecord,
)
from memo.report import HOUSE_STYLE, Citation, RenderedSection, render_markdown
from memo.report.render import Figure
from memo.report.structure import MEMO_SECTIONS, Section
from memo.trace import NULL_TRACER, RunSession, Tracer, new_run_id

from memo.compose import values

# A section returns a one-line takeaway (the conclusion + key figure, rendered emphasized)
# and a tight body. Splitting them forces the "so what" to the top and keeps the body short;
# citation markers stay inline in the body text.
_PROSE_SCHEMA = {
    "type": "object",
    "properties": {
        "takeaway": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["takeaway", "body"],
    "additionalProperties": False,
}

# The thesis is the one cross-module synthesis: a stance, the thesis itself, and the
# differentiated view (where_view_departs) -- a sharp, falsifiable variant read of what
# the price implies vs. where our evidence leads. The variant view is required and
# non-empty; it is the brief's core ask (a thesis, not a description).
_THESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string"},
        "thesis": {"type": "string"},
        "where_view_departs": {"type": "string", "minLength": 1},
    },
    "required": ["recommendation", "thesis", "where_view_departs"],
    "additionalProperties": False,
}

# Cross-module sections have no module of their own; these say which modules feed them.
# Section ids (not module names) to pull cross-module records from: section_records is
# keyed by section id, and the price module feeds the "valuation" section, so "valuation"
# is the key that surfaces its cash/balance-sheet claims and the rNPV record.
_OVERVIEW_SOURCES = ["moa", "regulatory", "valuation"]
_MAX_CROSS_CLAIMS = 12


# --------------------------------------------------------------------------- public

def compose_memo(
    company: str,
    model,
    storage: Optional[Storage] = None,
    ledger: Optional[LedgerStore] = None,
    tracer: Tracer = NULL_TRACER,
    session: Optional[RunSession] = None,
    trigger: str = "manual",
) -> str:
    """Compose a full memo for ``company`` and return its ``memo_id``.

    Runs every analysis module, persists their claims to ``ledger``, drafts and
    synthesizes every section, writes the rendered-memo JSON under
    ``<storage.root>/memos/<memo_id>.json``, and returns the ``memo_id`` (which is also
    the run id correlating the trace).

    A ``session`` (optional) accumulates each model call's token usage for this run; the
    caller passes one in to read its ``summary`` afterward.
    """
    started = time.monotonic()
    run_id = new_run_id(company)
    memo_id = run_id
    storage = storage or Storage()
    ledger = ledger or LedgerStore()

    session = session or RunSession()
    session.run_id = run_id
    session.model = session.model or getattr(model, "model", "")

    _attach_tracing(model, tracer, run_id)
    setattr(model, "session", session)  # so each complete_json records into this run

    context = AnalysisContext.for_company(company, model, storage)
    _attach_tracing(context.retriever, tracer, run_id)

    # 1. Run every analysis module. A module that raises degrades to an empty result so
    #    one failure never aborts the whole memo.
    results: Dict[str, AnalysisResult] = {}
    for name, module_cls in REGISTRY.items():
        results[name] = _run_module(name, module_cls, context, tracer, run_id)

    # 6a. Valuation (optional import): rNPV from the peak-sales figure and the PoS. Computed
    #     before persistence so its number becomes a stored valuation claim (value_num).
    rnpv, valuation_note = _compute_valuation(results)

    # 2/3. Map each module's claims to ledger records, tagged with the section they feed.
    section_records: Dict[str, List[ClaimRecord]] = {}
    for section in MEMO_SECTIONS:
        if section.module and section.module in results:
            section_records[section.id] = _to_claim_records(
                memo_id, section.id, results[section.module]
            )
    if rnpv is not None:
        section_records.setdefault("valuation", []).append(_rnpv_record(memo_id, rnpv))
    all_records: List[ClaimRecord] = [r for recs in section_records.values() for r in recs]

    # Per-section rollups (module confidence + tier + summary) for the dashboard's
    # conviction dots and metric-panel takeaways.
    section_rollups = _section_rollups(memo_id, results)

    any_grounded = any(r.evidence for r in all_records)

    # 6b. Figures (optional import), keyed to the section their claim belongs to.
    figures_by_section = _gather_figures(company, results, section_records, storage)

    # Market context (the same on-disk price cache the web app serves) so the thesis and
    # valuation size against the live price instead of disclaiming it.
    market = _market_context(company, storage)

    # 5. Synthesize the thesis across modules (needs the valuation).
    thesis = _synthesize_thesis(
        model, company, results, section_records, rnpv, market
    ) if any_grounded else None

    # 4/8. Draft each section in document order; track required sections left ungrounded.
    rendered: List[RenderedSection] = []
    failed_required: List[str] = []
    for section in MEMO_SECTIONS:
        rs, grounded = _render_section(
            section, model, company, results, section_records,
            all_records, any_grounded, rnpv, valuation_note, thesis,
            figures_by_section.get(section.id, []), market,
        )
        rendered.append(rs)
        if section.required and not grounded:
            failed_required.append(section.id)

    # 8. Refusal: a required section with no grounded claim is a memo we cannot honestly
    #    produce. Name the section(s) rather than fabricate.
    if failed_required:
        status = "refused"
        notes = (
            "refused: required section(s) had no grounded claims: "
            + ", ".join(failed_required)
        )
        recommendation = None
        thesis_text = None
        variant_view = None
    else:
        status = "complete"
        notes = valuation_note or ""
        recommendation = thesis["recommendation"] if thesis else None
        thesis_text = thesis["thesis"] if thesis else None
        variant_view = thesis["where_view_departs"] if thesis else None

    memo = MemoRecord(
        memo_id=memo_id,
        company=company,
        status=status,
        recommendation=recommendation,
        thesis=thesis_text,
        notes=notes,
    )
    ledger.write_memo(memo, all_records, section_rollups)
    ledger.write_generation_run(
        _generation_run(memo, all_records, rendered, session, trigger,
                        time.monotonic() - started)
    )

    _write_artifact(memo, rendered, storage, variant_view)
    return memo_id


def render_markdown_for(memo_id: str, storage: Optional[Storage] = None) -> str:
    """Load a written memo artifact and render it to Markdown via the report renderer."""
    storage = storage or Storage()
    memo = load_artifact(memo_id, storage)
    sections = [
        RenderedSection(
            section_id=s["section_id"],
            title=s["title"],
            prose=s["prose"],
            citations=[
                Citation(c["marker"], c["evidence_id"], c["url"], c.get("label", ""))
                for c in s.get("citations", [])
            ],
            figures=[
                Figure(f["figure_id"], f["caption"], f["image_ref"], f.get("claim_id"))
                for f in s.get("figures", [])
            ],
            takeaway=s.get("takeaway", ""),
        )
        for s in memo["sections"]
    ]
    return render_markdown(sections, memo["title"])


def load_artifact(memo_id: str, storage: Optional[Storage] = None) -> dict:
    """Read the rendered-memo JSON artifact for ``memo_id``."""
    storage = storage or Storage()
    return json.loads(_artifact_path(memo_id, storage).read_text())


# ----------------------------------------------------------------- module execution

def _run_module(name, module_cls, context, tracer, run_id) -> AnalysisResult:
    try:
        with tracer.span(run_id, "step", f"analyze:{name}") as event:
            result = module_cls().analyze(context)
            event["n_claims"] = len(result.claims)
            event["n_grounded"] = len(result.grounded_claims)
        return result
    except Exception as exc:  # noqa: BLE001 - one module failing must not abort the memo
        return AnalysisResult(
            company=context.company,
            module=name,
            summary="",
            notes=[f"module '{name}' failed: {type(exc).__name__}: {exc}"],
        )


def _to_claim_records(
    memo_id: str, section_id: str, result: AnalysisResult
) -> List[ClaimRecord]:
    """Map a module's analysis Claims (+ their Evidence) into ledger records."""
    records: List[ClaimRecord] = []
    for claim in result.claims:
        evidence = [
            EvidenceRecord(
                doc_id=ev.doc_id,
                source=ev.source,
                doc_type=ev.doc_type,
                url=ev.url,
                quote=ev.quote,
                date=ev.date,
                chunk_id=ev.chunk_id,
            )
            for ev in claim.evidence
        ]
        value_num, value_unit = values.parse_numeric(claim.value)
        records.append(
            ClaimRecord(
                memo_id=memo_id,
                section=section_id,
                module=result.module,
                statement=claim.statement,
                confidence=claim.confidence,
                rationale=claim.rationale,
                value=claim.value,
                value_num=value_num,
                value_unit=value_unit,
                evidence=evidence,
            )
        )
    return records


# Confidence -> tier thresholds. One place so the API and the engine agree.
_TIER_HIGH = 0.66
_TIER_MED = 0.40


def _tier(confidence: float) -> str:
    if confidence >= _TIER_HIGH:
        return "high"
    if confidence >= _TIER_MED:
        return "med"
    return "low"


def _section_rollups(memo_id: str, results: Dict[str, AnalysisResult]) -> List[SectionRecord]:
    """One rollup per module-backed section: its module confidence, tier, and summary."""
    rollups: List[SectionRecord] = []
    for section in MEMO_SECTIONS:
        module = section.module
        if not module or module not in results:
            continue
        result = results[module]
        rollups.append(
            SectionRecord(
                memo_id=memo_id,
                section_id=section.id,
                module=module,
                confidence=result.confidence,
                tier=_tier(result.confidence),
                summary=result.summary or "",
            )
        )
    return rollups


def _generation_run(
    memo: MemoRecord,
    all_records: List[ClaimRecord],
    rendered: List[RenderedSection],
    session: RunSession,
    trigger: str,
    duration_s: float,
) -> GenerationRunRecord:
    """Build the run-level summary (outcome + cost + tokens) for the Activity feed."""
    drafted = sum(
        1 for s in rendered
        if (s.prose or "").strip() and "not grounded" not in (s.prose or "").lower()
    )
    if memo.status == "refused":
        status, output, refusal = "refused", memo.notes, memo.notes
    else:
        status = "completed"
        output = f"{len(all_records)} claims · {drafted} sections"
        refusal = None
    tokens = session.tokens if session else {}
    return GenerationRunRecord(
        run_id=memo.memo_id,
        memo_id=memo.memo_id,
        company=memo.company,
        status=status,
        trigger=trigger,
        output=output,
        refusal_reason=refusal,
        model=getattr(session, "model", "") or "",
        input_tokens=int(tokens.get("input_tokens", 0)),
        output_tokens=int(tokens.get("output_tokens", 0)),
        cost_usd=round(session.cost(), 4) if session else 0.0,
        duration_s=round(duration_s, 2),
    )


def _rnpv_record(memo_id: str, rnpv: dict) -> ClaimRecord:
    """The deterministic rNPV as a persisted valuation claim (value_num = the number)."""
    pos = float(rnpv.get("pos", 0.0) or 0.0)
    return ClaimRecord(
        memo_id=memo_id,
        section="valuation",
        module="valuation",
        statement=(
            f"Risk-adjusted NPV (rNPV) of the lead asset is {rnpv.get('rnpv_str', 'n/a')}, "
            f"from the peak-sales estimate at a {pos:.0%} probability of success."
        ),
        confidence=pos,
        rationale=rnpv.get("note", ""),
        value=rnpv.get("rnpv_str"),
        value_num=rnpv.get("rnpv"),
        value_unit="USD",
        evidence=[],
    )


# ------------------------------------------------------------------- section drafting

# Forward-looking sections that are inherently assumption-based for a pre-commercial
# asset (no public epidemiology/pricing exists). These must be present and honestly
# flagged, but grounding is not the bar, so an all-assumptions section here does not
# refuse the whole memo. Grounding stays required for the evidence-driven core
# (moa/pos/regulatory) and sources. Aligns with the brief: commercial data has no
# programmatic source, so its absence is reported, not treated as disqualifying.
_ASSUMPTION_OK = {"peak_sales"}


def _render_section(
    section: Section,
    model,
    company: str,
    results: Dict[str, AnalysisResult],
    section_records: Dict[str, List[ClaimRecord]],
    all_records: List[ClaimRecord],
    any_grounded: bool,
    rnpv: Optional[dict],
    valuation_note: str,
    thesis: Optional[dict],
    figures: List[Figure],
    market: Optional[dict] = None,
) -> Tuple[RenderedSection, bool]:
    """Render one section; return it plus whether it is grounded (for the refusal check)."""
    sid = section.id

    if sid == "sources":
        rs = _render_sources(section, all_records)
        rs.figures = figures
        return rs, bool(rs.citations)

    if sid == "thesis":
        rs = _render_thesis(section, thesis, section_records, results)
        rs.figures = figures
        return rs, thesis is not None

    if sid == "overview":
        records = _cross_module_records(section_records, _OVERVIEW_SOURCES)
        grounded = [r for r in records if r.evidence]
        if not grounded:
            return _placeholder(section), False
        takeaway, prose, citations = _draft_prose(
            model, company, section, grounded,
            extra="Introduce the company, its lead assets and development stage, and its "
                  "cash position, staying strictly within the claims provided.",
            max_words=110,
        )
        return RenderedSection(sid, section.title, prose, citations, figures,
                               takeaway=takeaway), True

    if sid in ("risks", "catalysts"):
        # Optional sections: draw from the most-confident grounded claims (not all 100+),
        # rendered as a short ranked list.
        grounded = sorted(
            [r for r in all_records if r.evidence], key=lambda r: r.confidence, reverse=True
        )[:8]
        if not grounded:
            return RenderedSection(sid, section.title, "", [], figures), False
        extra = (
            "State the key risks to the thesis that these claims imply: a claim's "
            "weakness, gap, assumed input, or downside. Do not introduce risks the "
            "claims do not support."
            if sid == "risks" else
            "List the upcoming readouts, filings, or decisions these claims point to "
            "that would move the view. If the claims name no dated catalyst, say so."
        )
        takeaway, prose, citations = _draft_prose(
            model, company, section, grounded, extra=extra, max_words=120, bullets=True
        )
        return RenderedSection(sid, section.title, prose, citations, figures,
                               takeaway=takeaway), True

    # A module-backed section (moa, pos, regulatory, peak_sales, valuation via price).
    records = section_records.get(sid, [])
    grounded = [r for r in records if r.evidence]

    extra = ""
    if sid == "valuation":
        extra = _valuation_instruction(rnpv, valuation_note, market)
    elif sid == "peak_sales":
        extra = ("This commercial estimate rests on assumed inputs; no public epidemiology "
                 "or pricing exists for a pre-commercial asset. State that plainly and "
                 "present the range as illustrative, not a forecast.")
    elif sid == "regulatory":
        extra = ("Give one bullet per program or asset. Lead each bullet with the "
                 "program/asset name followed by a colon, then its regulatory status and "
                 "the dates that matter (designation, filing, PDUFA). End with one bullet "
                 "noting any key open regulatory question (e.g. accelerated vs full approval).")

    # List-heavy sections (regulatory) draft as per-item bullets with more room so every
    # program fits; narrative sections keep the default prose shape.
    bullets = getattr(section, "layout", "prose") == "bullets"
    max_words = 190 if bullets else 150

    if sid in _ASSUMPTION_OK:
        # Draft from all its claims (grounded plus flagged assumptions); presence, not
        # grounding, is the bar, so an all-assumptions commercial section is acceptable.
        usable = grounded or records
        if not usable:
            return _placeholder(section), False
        takeaway, prose, citations = _draft_prose(
            model, company, section, usable, extra=extra, max_words=max_words, bullets=bullets
        )
        return RenderedSection(sid, section.title, prose, citations, figures,
                               takeaway=takeaway), True

    if not grounded:
        return _placeholder(section), False
    takeaway, prose, citations = _draft_prose(
        model, company, section, grounded, extra=extra, max_words=max_words, bullets=bullets
    )
    return RenderedSection(sid, section.title, prose, citations, figures,
                           takeaway=takeaway), True


def _draft_prose(
    model,
    company: str,
    section: Section,
    records: List[ClaimRecord],
    extra: str = "",
    max_words: int = 150,
    bullets: bool = False,
) -> Tuple[str, str, List[Citation]]:
    """Draft one section: return (takeaway, body, citations).

    Evidence across the records is deduped into a stable marker set ([1], [2], ...); the
    drafter is handed those markers and told to cite them inline and invent nothing. The
    takeaway is a one-line lead; the body is capped at ``max_words``.
    """
    marker_by_evidence, citations = _build_markers(records)
    user = _section_prompt(company, section, records, marker_by_evidence, extra,
                           max_words, bullets)
    try:
        response = model.complete_json(HOUSE_STYLE, user, _PROSE_SCHEMA)
        takeaway = str(response.data.get("takeaway", "")).strip()
        body = str(response.data.get("body", "")).strip()
    except Exception as exc:  # noqa: BLE001 - a drafting failure degrades to a stub note
        takeaway = ""
        body = f"[drafting unavailable for {section.title}: {type(exc).__name__}]"
    return takeaway, body, citations


def _section_prompt(company, section, records, marker_by_evidence, extra,
                    max_words=150, bullets=False) -> str:
    body_shape = (
        f"a BODY as bullet lines (start each line with '- '): one bullet per item "
        f"(program, asset, event, risk, or catalyst as the section calls for), most "
        f"material first; begin each bullet with its subject followed by a colon, then "
        f"only the status and the one or two facts that matter, with dates; keep each "
        f"bullet to a single line; omit minor items; at most {max_words} words total"
        if bullets else
        f"a BODY of at most {max_words} words; front-load the conclusion and include only "
        f"the few claims that change the view"
    )
    lines = [
        f"Company: {company}",
        f"Section: {section.title} — {section.description}",
        "",
        "Write this section using ONLY the verified claims below. Cite evidence inline "
        "with the bracket markers exactly as given ([1], [2], ...). Do not introduce any "
        "fact, name, number, or comparison beyond these claims, and do not restate or "
        "redo arithmetic — use the figures as provided.",
        f"Return a one-sentence TAKEAWAY (the conclusion plus the single most important "
        f"number) and {body_shape}. Be concise; do not cover every claim.",
    ]
    if extra:
        lines.append(extra)
    lines += ["", "Claims:"]
    for record in records:
        markers = " ".join(
            marker_by_evidence[ev.evidence_id] for ev in record.evidence
            if ev.evidence_id in marker_by_evidence
        )
        value = f" (value: {record.value})" if record.value else ""
        lines.append(
            f"- {record.statement}{value} [confidence {record.confidence:.2f}] "
            f"cites {markers or '(none)'}"
        )
        if record.rationale:
            lines.append(f"    rationale: {record.rationale}")
    lines += ["", "Evidence markers:"]
    seen: set = set()
    for record in records:
        for ev in record.evidence:
            marker = marker_by_evidence.get(ev.evidence_id)
            if not marker or marker in seen:
                continue
            seen.add(marker)
            quote = (ev.quote or "")[:240]
            lines.append(f"{marker} {ev.source} {ev.doc_type} — {ev.url} — \"{quote}\"")
    lines += ["", 'Return JSON: {"prose": "..."} with the [n] markers inline.']
    return "\n".join(lines)


def _build_markers(
    records: List[ClaimRecord],
) -> Tuple[Dict[str, str], List[Citation]]:
    """Dedupe evidence across records into [1], [2], ... and matching Citation rows."""
    marker_by_evidence: Dict[str, str] = {}
    citations: List[Citation] = []
    for record in records:
        for ev in record.evidence:
            if ev.evidence_id in marker_by_evidence:
                continue
            marker = f"[{len(citations) + 1}]"
            marker_by_evidence[ev.evidence_id] = marker
            citations.append(
                Citation(
                    marker=marker,
                    evidence_id=ev.evidence_id,
                    url=ev.url,
                    label=_label(ev),
                )
            )
    return marker_by_evidence, citations


def _label(ev: EvidenceRecord) -> str:
    """A short, distinguishing citation label (source + doc id, e.g. "pubmed 34118902").

    Prefer the document id so chips are traceable, not generic ("pubmed article"). Kept in
    sync with the read-side label in backend/app/services.py.
    """
    src = (ev.source or "").strip()
    if src == "clinicaltrials":
        return ev.doc_id or "clinicaltrials"
    if ev.doc_id:
        return f"{src} {ev.doc_id}".strip() if src else ev.doc_id
    dt = (ev.doc_type or "").strip()
    return (f"{src} {dt}".strip()) or src or "source"


def _placeholder(section: Section) -> RenderedSection:
    """Honest stand-in for a section with no grounded claim (never fabricated prose)."""
    return RenderedSection(
        section_id=section.id,
        title=section.title,
        prose=(
            f"Not grounded: the available evidence does not support any claim for "
            f"{section.title.lower()}. This gap is reported rather than filled."
        ),
        citations=[],
    )


# --------------------------------------------------------------------------- thesis

def _synthesize_thesis(
    model,
    company: str,
    results: Dict[str, AnalysisResult],
    section_records: Dict[str, List[ClaimRecord]],
    rnpv: Optional[dict],
    market: Optional[dict] = None,
) -> Optional[dict]:
    """Produce the cross-module recommendation + thesis + where-our-view-departs."""
    records = _cross_module_records(
        section_records, ["moa", "pos", "regulatory", "peak_sales", "valuation"], per_module=2
    )
    grounded = [r for r in records if r.evidence]
    if not grounded:
        return None

    marker_by_evidence, _ = _build_markers(grounded)
    lines = [f"Company: {company}", ""]
    for module_name, result in results.items():
        if result.summary:
            lines.append(f"{module_name} summary: {result.summary}")
    if rnpv is not None:
        lines += [
            "",
            f"Deterministic valuation: rNPV {rnpv.get('rnpv_str', 'n/a')} "
            f"(unadjusted NPV ${rnpv.get('unadjusted_npv', 0.0) / 1e9:.1f}B, "
            f"pos {rnpv.get('pos', 0.0):.2f}). {rnpv.get('note', '')}",
        ]
    else:
        lines += ["", "Valuation: no rNPV computed (peak sales or PoS not available)."]
    if market and market.get("text"):
        lines += [
            "",
            f"Market (live cache): {market['text']}. The rNPV is lead-asset only against a "
            "whole-company market cap, so size the thesis against this market value as a "
            "coverage read, not naive upside, and state where our view departs from the price.",
        ]
    lines += ["", "Key grounded claims:"]
    for record in grounded:
        markers = " ".join(
            marker_by_evidence[ev.evidence_id] for ev in record.evidence
            if ev.evidence_id in marker_by_evidence
        )
        value = f" (value: {record.value})" if record.value else ""
        lines.append(f"- [{record.module}] {record.statement}{value} cites {markers}")
    lines += [
        "",
        "Task: synthesize the cross-module thesis. Return three fields.",
        "",
        "recommendation: a one-line stance of at most 15 words (e.g. 'Constructive; "
        "size for the binary readout').",
        "",
        "thesis: at most 100 words. Lead with the trade and the single number that "
        "carries it. Use only the claims and the valuation above.",
        "",
        "where_view_departs: the most important field, a sharp and falsifiable variant "
        "view a PM could disagree with. It MUST contain all four of:",
        "  1. What the price implies now. Construct the implied expectation from the "
        "price, the market cap, and the rNPV-vs-cap coverage above (Street consensus has "
        "no programmatic source, so infer it): say what the market is effectively "
        "pricing, e.g. near-approval, a heavy failure discount, or little value for the "
        "lead asset.",
        "  2. Where our evidence leads to a DIFFERENT conclusion, tied to one specific "
        "claim (mechanism, probability of success, regulatory path, or peak sales), not a "
        "hedge.",
        "  3. Why, from that specific evidence, quantified where possible: the direction "
        "and rough size of the gap (e.g. 'PoS nearer 50% vs the ~25% the price implies').",
        "  4. The catalyst or datapoint that resolves it and roughly when: what would "
        "prove us right or wrong.",
        "",
        "Forbidden: vague, non-falsifiable lines such as 'the market may be "
        "underappreciating the opportunity'. Name the number and the claim. Cite the [n] "
        "markers where you lean on a specific claim. Be candid about gaps.",
    ]
    try:
        response = model.complete_json(HOUSE_STYLE, "\n".join(lines), _THESIS_SCHEMA)
        data = response.data
    except Exception:  # noqa: BLE001 - a thesis failure leaves the memo without one
        return None
    return {
        "recommendation": str(data.get("recommendation", "")).strip(),
        "thesis": str(data.get("thesis", "")).strip(),
        "where_view_departs": str(data.get("where_view_departs", "")).strip(),
    }


def _render_thesis(section, thesis, section_records, results) -> RenderedSection:
    if thesis is None:
        return _placeholder(section)
    records = _cross_module_records(
        section_records, ["moa", "pos", "regulatory", "peak_sales", "valuation"], per_module=2
    )
    grounded = [r for r in records if r.evidence]
    _, citations = _build_markers(grounded)
    prose = thesis["thesis"]
    if thesis.get("where_view_departs"):
        prose += "\n\nWhere our view departs: " + thesis["where_view_departs"]
    return RenderedSection(section.id, section.title, prose.strip(), citations,
                           takeaway=thesis.get("recommendation", ""))


# ---------------------------------------------------------------- valuation & figures

def _compute_valuation(
    results: Dict[str, AnalysisResult]
) -> Tuple[Optional[dict], str]:
    """rNPV from the peak-sales figure and the PoS, via the optional valuation module."""
    peak = values.peak_sales_usd(results.get("peak_sales"))
    pos = values.pos_fraction(results.get("pos"))
    if peak is None or pos is None:
        missing = []
        if peak is None:
            missing.append("peak sales")
        if pos is None:
            missing.append("probability of success")
        return None, f"rNPV not computed: missing {', '.join(missing)}."
    try:
        from memo.analysis.valuation import compute_rnpv
    except ImportError:
        return None, "rNPV not computed: valuation model unavailable."
    try:
        return compute_rnpv(peak, pos), ""
    except Exception as exc:  # noqa: BLE001 - degrade rather than fail the memo
        return None, f"rNPV not computed: valuation error ({type(exc).__name__})."


def _valuation_instruction(rnpv: Optional[dict], valuation_note: str,
                           market: Optional[dict] = None) -> str:
    has_market = bool(market and market.get("market_cap"))
    market_txt = f" Market (live cache): {market['text']}." if market and market.get("text") else ""
    if rnpv is not None:
        anchor = (
            " The rNPV covers the lead asset only while the market cap is the whole company, "
            "so read rNPV against market cap as a coverage ratio, not upside; say where our "
            "read departs from what the price implies."
            if has_market else
            " No live market price is in the corpus, so do not size upside against price."
        )
        return (
            "Frame the rNPV valuation against the balance-sheet claims."
            f"{market_txt} Deterministic rNPV: {rnpv.get('rnpv_str', 'n/a')} (unadjusted NPV "
            f"${rnpv.get('unadjusted_npv', 0.0) / 1e9:.1f}B at pos "
            f"{rnpv.get('pos', 0.0):.2f}). Present it as an order-of-magnitude figure, not a "
            f"precise target.{anchor}"
        )
    tail = (
        " Size the price-vs-thesis read against the market cap above as a coverage read."
        if has_market else
        " Keep the price-vs-thesis read conditional on a market price supplied elsewhere."
    )
    return f"No rNPV was computed ({valuation_note}). State this gap plainly.{market_txt}{tail}"


def _market_context(company: str, storage: Storage) -> Optional[dict]:
    """Latest cached market snapshot (price / market cap) for the company, or None.

    Reads the same on-disk price cache the web app serves from, so the memo sizes its read
    against the live market instead of disclaiming it. Best-effort: a missing file or field
    yields None rather than failing the memo.
    """
    try:
        snap = json.loads((Path(storage.root) / "price_snapshots.json").read_text())
        snap = snap.get(company.upper())
    except Exception:  # noqa: BLE001 - market context is optional
        return None
    if not snap:
        return None
    price, cap = snap.get("market_price"), snap.get("market_cap")
    if price is None and cap is None:
        return None
    parts = []
    if price is not None:
        parts.append(f"price ${price:,.2f}")
    if cap is not None:
        parts.append(f"market cap ${cap / 1e9:.2f}B")
    return {"market_price": price, "market_cap": cap,
            "shares": snap.get("shares"), "text": ", ".join(parts)}


def _gather_figures(
    company: str,
    results: Dict[str, AnalysisResult],
    section_records: Dict[str, List[ClaimRecord]],
    storage: Storage,
) -> Dict[str, List[Figure]]:
    """Figures per section, via the optional figures package. Empty when unavailable."""
    try:
        from memo.figures import figures_for_claims
    except ImportError:
        return {}
    claims: List[Claim] = []
    for result in results.values():
        claims.extend(result.claims)
    try:
        figures = figures_for_claims(company, claims, storage) or []
    except Exception:  # noqa: BLE001 - figures are decorative; never fail the memo
        return {}

    # Map a figure to the section of the claim it annotates; unmatched -> peak_sales.
    claim_to_section: Dict[str, str] = {}
    for section_id, records in section_records.items():
        for record in records:
            claim_to_section[record.claim_id] = section_id
    by_section: Dict[str, List[Figure]] = {}
    for figure in figures:
        section_id = claim_to_section.get(getattr(figure, "claim_id", None), "peak_sales")
        by_section.setdefault(section_id, []).append(figure)
    return by_section


# --------------------------------------------------------------------------- sources

def _render_sources(section: Section, all_records: List[ClaimRecord]) -> RenderedSection:
    """The evidence appendix: every distinct document cited across the memo."""
    _, citations = _build_markers(all_records)
    if not citations:
        return _placeholder(section)
    prose = (
        f"Evidence appendix. This memo rests on {len(citations)} cited source"
        f"{'s' if len(citations) != 1 else ''}; each is linked below and referenced by "
        "its marker where it appears in the memo."
    )
    return RenderedSection(section.id, section.title, prose, citations)


# ------------------------------------------------------------------------- artifact

def _render_to_dict(
    memo: MemoRecord, sections: List[RenderedSection], variant_view: Optional[str] = None
) -> dict:
    return {
        "memo_id": memo.memo_id,
        "company": memo.company,
        "title": f"{memo.company} Investment Memo",
        "recommendation": memo.recommendation,
        "thesis": memo.thesis,
        # The differentiated view, surfaced as a distinct top-level field so the UI can
        # feature it rather than dig it out of the thesis prose.
        "variant_view": variant_view,
        "status": memo.status,
        "sections": [
            {
                "section_id": s.section_id,
                "title": s.title,
                "takeaway": s.takeaway,
                "prose": s.prose,
                "citations": [
                    {
                        "marker": c.marker,
                        "evidence_id": c.evidence_id,
                        "url": c.url,
                        "label": c.label,
                    }
                    for c in s.citations
                ],
                "figures": [
                    {
                        "figure_id": f.figure_id,
                        "caption": f.caption,
                        "image_ref": f.image_ref,
                        "claim_id": f.claim_id,
                    }
                    for f in s.figures
                ],
            }
            for s in sections
        ],
    }


def _write_artifact(
    memo: MemoRecord, sections: List[RenderedSection], storage: Storage,
    variant_view: Optional[str] = None,
) -> Path:
    path = _artifact_path(memo.memo_id, storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_render_to_dict(memo, sections, variant_view), indent=2))
    return path


def _artifact_path(memo_id: str, storage: Storage) -> Path:
    return Path(storage.root) / "memos" / f"{memo_id}.json"


# --------------------------------------------------------------------------- helpers

def _cross_module_records(
    section_records: Dict[str, List[ClaimRecord]],
    section_ids: List[str],
    per_module: Optional[int] = None,
    cap: int = _MAX_CROSS_CLAIMS,
) -> List[ClaimRecord]:
    """Grounded claim records pulled from several sections for a cross-module section.

    ``per_module`` caps how many of each section's grounded claims are taken (most
    confident first) so the synthesis prompt stays balanced and bounded.
    """
    collected: List[ClaimRecord] = []
    for section_id in section_ids:
        records = [r for r in section_records.get(section_id, []) if r.evidence]
        records.sort(key=lambda r: r.confidence, reverse=True)
        if per_module is not None:
            records = records[:per_module]
        collected.extend(records)
    return collected[:cap]


def _attach_tracing(target, tracer: Tracer, run_id: str) -> None:
    """Point a model client / retriever at this run's tracer so its calls are traced."""
    for attr, value in (("tracer", tracer), ("run_id", run_id)):
        try:
            setattr(target, attr, value)
        except Exception:  # noqa: BLE001 - tracing hookup is best-effort
            pass
