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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from memo.analysis import REGISTRY, AnalysisContext, AnalysisResult
from memo.analysis.base import Claim
from memo.ingestion.base import Storage
from memo.ledger import ClaimRecord, EvidenceRecord, LedgerStore, MemoRecord
from memo.report import HOUSE_STYLE, Citation, RenderedSection, render_markdown
from memo.report.render import Figure
from memo.report.structure import MEMO_SECTIONS, Section
from memo.trace import NULL_TRACER, Tracer, new_run_id

from memo.compose import values

# A section's prose comes back as a single JSON field so the drafter cannot smuggle
# extra structure past the house style; markers stay inline in the prose text.
_PROSE_SCHEMA = {
    "type": "object",
    "properties": {"prose": {"type": "string"}},
    "required": ["prose"],
    "additionalProperties": False,
}

# The thesis is the one cross-module synthesis: a stance, the thesis itself, and the
# explicit place our read departs from what the price implies.
_THESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string"},
        "thesis": {"type": "string"},
        "where_view_departs": {"type": "string"},
    },
    "required": ["recommendation", "thesis", "where_view_departs"],
    "additionalProperties": False,
}

# Cross-module sections have no module of their own; these say which modules feed them.
_OVERVIEW_SOURCES = ["moa", "regulatory", "price"]
_MAX_CROSS_CLAIMS = 12


# --------------------------------------------------------------------------- public

def compose_memo(
    company: str,
    model,
    storage: Optional[Storage] = None,
    ledger: Optional[LedgerStore] = None,
    tracer: Tracer = NULL_TRACER,
) -> str:
    """Compose a full memo for ``company`` and return its ``memo_id``.

    Runs every analysis module, persists their claims to ``ledger``, drafts and
    synthesizes every section, writes the rendered-memo JSON under
    ``<storage.root>/memos/<memo_id>.json``, and returns the ``memo_id`` (which is also
    the run id correlating the trace).
    """
    run_id = new_run_id(company)
    memo_id = run_id
    storage = storage or Storage()
    ledger = ledger or LedgerStore()

    _attach_tracing(model, tracer, run_id)

    context = AnalysisContext.for_company(company, model, storage)
    _attach_tracing(context.retriever, tracer, run_id)

    # 1. Run every analysis module. A module that raises degrades to an empty result so
    #    one failure never aborts the whole memo.
    results: Dict[str, AnalysisResult] = {}
    for name, module_cls in REGISTRY.items():
        results[name] = _run_module(name, module_cls, context, tracer, run_id)

    # 2/3. Map each module's claims to ledger records, tagged with the section they feed.
    section_records: Dict[str, List[ClaimRecord]] = {}
    for section in MEMO_SECTIONS:
        if section.module and section.module in results:
            section_records[section.id] = _to_claim_records(
                memo_id, section.id, results[section.module]
            )
    all_records: List[ClaimRecord] = [r for recs in section_records.values() for r in recs]

    any_grounded = any(r.evidence for r in all_records)

    # 6a. Valuation (optional import): rNPV from the peak-sales figure and the PoS.
    rnpv, valuation_note = _compute_valuation(results)

    # 6b. Figures (optional import), keyed to the section their claim belongs to.
    figures_by_section = _gather_figures(company, results, section_records, storage)

    # 5. Synthesize the thesis across modules (needs the valuation).
    thesis = _synthesize_thesis(
        model, company, results, section_records, rnpv
    ) if any_grounded else None

    # 4/8. Draft each section in document order; track required sections left ungrounded.
    rendered: List[RenderedSection] = []
    failed_required: List[str] = []
    for section in MEMO_SECTIONS:
        rs, grounded = _render_section(
            section, model, company, results, section_records,
            all_records, any_grounded, rnpv, valuation_note, thesis,
            figures_by_section.get(section.id, []),
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
    else:
        status = "complete"
        notes = valuation_note or ""
        recommendation = thesis["recommendation"] if thesis else None
        thesis_text = thesis["thesis"] if thesis else None

    memo = MemoRecord(
        memo_id=memo_id,
        company=company,
        status=status,
        recommendation=recommendation,
        thesis=thesis_text,
        notes=notes,
    )
    ledger.write_memo(memo, all_records)

    _write_artifact(memo, rendered, storage)
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
        records.append(
            ClaimRecord(
                memo_id=memo_id,
                section=section_id,
                module=result.module,
                statement=claim.statement,
                confidence=claim.confidence,
                rationale=claim.rationale,
                value=claim.value,
                evidence=evidence,
            )
        )
    return records


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
        prose, citations = _draft_prose(
            model, company, section, grounded,
            extra="Introduce the company, its lead assets and development stage, and its "
                  "cash position, staying strictly within the claims provided.",
        )
        return RenderedSection(sid, section.title, prose, citations, figures), True

    if sid in ("risks", "catalysts"):
        # Optional sections: draw from the whole grounded claim set; omit prose if bare.
        grounded = [r for r in all_records if r.evidence]
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
        prose, citations = _draft_prose(model, company, section, grounded, extra=extra)
        return RenderedSection(sid, section.title, prose, citations, figures), True

    # A module-backed section (moa, pos, regulatory, peak_sales, valuation via price).
    records = section_records.get(sid, [])
    grounded = [r for r in records if r.evidence]

    extra = ""
    if sid == "valuation":
        extra = _valuation_instruction(rnpv, valuation_note)
    elif sid == "peak_sales":
        extra = ("This commercial estimate rests on assumed inputs; no public epidemiology "
                 "or pricing exists for a pre-commercial asset. State that plainly and "
                 "present the range as illustrative, not a forecast.")

    if sid in _ASSUMPTION_OK:
        # Draft from all its claims (grounded plus flagged assumptions); presence, not
        # grounding, is the bar, so an all-assumptions commercial section is acceptable.
        usable = grounded or records
        if not usable:
            return _placeholder(section), False
        prose, citations = _draft_prose(model, company, section, usable, extra=extra)
        return RenderedSection(sid, section.title, prose, citations, figures), True

    if not grounded:
        return _placeholder(section), False
    prose, citations = _draft_prose(model, company, section, grounded, extra=extra)
    return RenderedSection(sid, section.title, prose, citations, figures), True


def _draft_prose(
    model,
    company: str,
    section: Section,
    records: List[ClaimRecord],
    extra: str = "",
) -> Tuple[str, List[Citation]]:
    """Draft one section's prose from grounded claim records; return prose + citations.

    Evidence across the records is deduped into a stable marker set ([1], [2], ...); the
    drafter is handed those markers and told to cite them inline and invent nothing.
    """
    marker_by_evidence, citations = _build_markers(records)
    user = _section_prompt(company, section, records, marker_by_evidence, extra)
    try:
        response = model.complete_json(HOUSE_STYLE, user, _PROSE_SCHEMA)
        prose = str(response.data.get("prose", "")).strip()
    except Exception as exc:  # noqa: BLE001 - a drafting failure degrades to a stub note
        prose = f"[drafting unavailable for {section.title}: {type(exc).__name__}]"
    return prose, citations


def _section_prompt(company, section, records, marker_by_evidence, extra) -> str:
    lines = [
        f"Company: {company}",
        f"Section: {section.title} — {section.description}",
        "",
        "Write this section using ONLY the verified claims below. Cite evidence inline "
        "with the bracket markers exactly as given ([1], [2], ...). Do not introduce any "
        "fact, name, number, or comparison beyond these claims, and do not restate or "
        "redo arithmetic — use the figures as provided.",
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
    if ev.source == "clinicaltrials":
        return ev.doc_id or "clinicaltrials"
    dt = (ev.doc_type or "").strip()
    return (f"{ev.source} {dt}".strip()) or (ev.doc_id or ev.source or "source")


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
) -> Optional[dict]:
    """Produce the cross-module recommendation + thesis + where-our-view-departs."""
    records = _cross_module_records(
        section_records, ["moa", "pos", "regulatory", "peak_sales", "price"], per_module=2
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
        "Task: synthesize the investment thesis across the modules above. Give a "
        "recommendation (a clear stance, calibrated to the evidence), the thesis itself, "
        "and where our view departs from what the market price implies. Use only the "
        "claims and the valuation provided; cite evidence markers inline where you lean "
        "on a specific claim. Be candid about gaps.",
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
        section_records, ["moa", "pos", "regulatory", "peak_sales", "price"], per_module=2
    )
    grounded = [r for r in records if r.evidence]
    _, citations = _build_markers(grounded)
    prose = thesis["thesis"]
    if thesis.get("where_view_departs"):
        prose += "\n\nWhere our view departs: " + thesis["where_view_departs"]
    return RenderedSection(section.id, section.title, prose.strip(), citations)


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


def _valuation_instruction(rnpv: Optional[dict], valuation_note: str) -> str:
    if rnpv is not None:
        return (
            "Frame the rNPV valuation against the balance-sheet claims. Deterministic "
            f"rNPV: {rnpv.get('rnpv_str', 'n/a')} (unadjusted NPV "
            f"${rnpv.get('unadjusted_npv', 0.0) / 1e9:.1f}B at pos "
            f"{rnpv.get('pos', 0.0):.2f}). Present it as an order-of-magnitude figure, "
            "not a precise target, and note the corpus lacks a live price and consensus."
        )
    return (
        f"No rNPV was computed ({valuation_note}). State this gap plainly and keep the "
        "price-vs-thesis read conditional on a market price supplied elsewhere."
    )


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

def _render_to_dict(memo: MemoRecord, sections: List[RenderedSection]) -> dict:
    return {
        "memo_id": memo.memo_id,
        "company": memo.company,
        "title": f"{memo.company} Investment Memo",
        "recommendation": memo.recommendation,
        "thesis": memo.thesis,
        "status": memo.status,
        "sections": [
            {
                "section_id": s.section_id,
                "title": s.title,
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
    memo: MemoRecord, sections: List[RenderedSection], storage: Storage
) -> Path:
    path = _artifact_path(memo.memo_id, storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_render_to_dict(memo, sections), indent=2))
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
