"""Flask app factory and routes for the memo audit surface.

Routes
------
``GET  /``                                  memo index (from the ledger)
``GET  /memo/<memo_id>``                     rendered memo (prose from JSON, ordered by
                                             MEMO_SECTIONS), with linkified citations
``GET  /memo/<memo_id>/audit/<evidence_id>`` claim-audit detail (evidence from the ledger)
``GET  /memo/<memo_id>/export.md``           the memo as Markdown (reuses report.render_markdown)

Reads are split by source: the ledger (LedgerStore) backs the index and every audit view;
the rendered-memo JSON backs the prose/citations/figures a reader sees.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from flask import Flask, abort, render_template, send_from_directory, url_for
from markupsafe import Markup, escape

from memo.ledger import ClaimRecord, EvidenceRecord, LedgerStore
from memo.report.render import Citation, Figure, RenderedSection, render_markdown
from memo.report.structure import MEMO_SECTIONS

DEFAULT_DB_PATH = "data/ledger.db"
DEFAULT_MEMOS_DIR = "data/memos"
DEFAULT_DATA_DIR = "data"

# Image types the figure route is allowed to serve.
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def create_app(
    db_path: Optional[str] = None,
    memos_dir: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> Flask:
    """Build the Flask app. ``db_path``/``memos_dir``/``data_dir`` override the defaults (tests use this)."""
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or DEFAULT_DB_PATH
    app.config["MEMOS_DIR"] = memos_dir or DEFAULT_MEMOS_DIR
    # Figures live under the same data root as the ledger/memos. Resolve to an absolute
    # path: send_from_directory joins a relative dir onto the app package root (app/),
    # not the cwd, so a bare "data" would look under app/data and 404.
    app.config["DATA_DIR"] = str(Path(data_dir or DEFAULT_DATA_DIR).resolve())

    def store() -> LedgerStore:
        # One short-lived connection per request keeps sqlite happy across threads.
        return LedgerStore(db_path=app.config["DB_PATH"])

    def load_rendered(memo_id: str) -> Optional[dict]:
        path = Path(app.config["MEMOS_DIR"]) / f"{memo_id}.json"
        if not path.exists():
            return None
        with path.open() as fh:
            return json.load(fh)

    # ---------------------------------------------------------------- index

    @app.route("/")
    def index():
        s = store()
        try:
            memos = s.list_memos()
        finally:
            s.close()
        return render_template("list.html", memos=memos)

    # ------------------------------------------------------------ memo view

    @app.route("/memo/<memo_id>")
    def memo_view(memo_id: str):
        s = store()
        try:
            memo = s.get_memo(memo_id)
            if memo is None:
                abort(404)
            rendered = load_rendered(memo_id)
            has_artifact = rendered is not None
            sections = _ordered_sections(rendered) if rendered else []
        finally:
            s.close()

        view_sections = [
            _section_for_template(memo_id, app.config["DATA_DIR"], sec)
            for sec in sections
        ]
        return render_template(
            "memo.html",
            memo=memo,
            has_artifact=has_artifact,
            title=(rendered or {}).get("title") or f"{memo.company} memo",
            sections=view_sections,
        )

    # --------------------------------------------------------- claim audit

    @app.route("/memo/<memo_id>/audit/<evidence_id>")
    def audit(memo_id: str, evidence_id: str):
        s = store()
        try:
            memo = s.get_memo(memo_id)
            if memo is None:
                abort(404)
            found = _find_evidence(s, memo_id, evidence_id)
        finally:
            s.close()
        if found is None:
            abort(404)
        claim, evidence = found
        return render_template(
            "audit.html", memo=memo, claim=claim, evidence=evidence
        )

    # -------------------------------------------------------------- export

    @app.route("/memo/<memo_id>/export.md")
    def export_markdown(memo_id: str):
        s = store()
        try:
            memo = s.get_memo(memo_id)
            if memo is None:
                abort(404)
            rendered = load_rendered(memo_id)
        finally:
            s.close()
        if rendered is None:
            abort(404)
        title = rendered.get("title") or f"{memo.company} Investment Memo"
        sections = [_to_rendered_section(sec) for sec in _ordered_sections(rendered)]
        md = render_markdown(sections, title)
        # served as text so a browser shows it / a client can save it
        return app.response_class(md, mimetype="text/markdown")

    # ------------------------------------------------------------- figures

    @app.route("/figures/<path:filename>")
    def figure(filename: str):
        # Only serve image files, and never let the path escape the data root.
        if Path(filename).suffix.lower() not in _IMAGE_EXTENSIONS:
            abort(404)
        # send_from_directory rejects any path that resolves outside DATA_DIR.
        return send_from_directory(app.config["DATA_DIR"], filename)

    return app


# --------------------------------------------------------------------- helpers


def _ordered_sections(rendered: dict) -> List[dict]:
    """Return the artifact's sections in MEMO_SECTIONS document order.

    Any section not named in MEMO_SECTIONS is appended after the known ones, preserving
    the artifact's own order, so nothing silently disappears.
    """
    by_id: Dict[str, dict] = {
        sec.get("section_id"): sec for sec in rendered.get("sections", [])
    }
    ordered: List[dict] = []
    seen = set()
    for spec in MEMO_SECTIONS:
        sec = by_id.get(spec.id)
        if sec is not None:
            # trust MEMO_SECTIONS for the canonical title, fall back to the artifact's
            merged = dict(sec)
            merged.setdefault("title", spec.title)
            merged["title"] = sec.get("title") or spec.title
            ordered.append(merged)
            seen.add(spec.id)
    for sec in rendered.get("sections", []):
        if sec.get("section_id") not in seen:
            ordered.append(sec)
    return ordered


def _to_rendered_section(sec: dict) -> RenderedSection:
    """Rebuild a report.RenderedSection from an artifact section dict (for export)."""
    citations = [
        Citation(
            marker=c.get("marker", ""),
            evidence_id=c.get("evidence_id", ""),
            url=c.get("url", ""),
            label=c.get("label", ""),
        )
        for c in sec.get("citations", [])
    ]
    figures = [
        Figure(
            figure_id=f.get("figure_id", ""),
            caption=f.get("caption", ""),
            image_ref=f.get("image_ref", ""),
            claim_id=f.get("claim_id"),
        )
        for f in sec.get("figures", [])
    ]
    return RenderedSection(
        section_id=sec.get("section_id", ""),
        title=sec.get("title", ""),
        prose=sec.get("prose", ""),
        citations=citations,
        figures=figures,
    )


def _section_for_template(memo_id: str, data_dir: str, sec: dict) -> dict:
    """Shape one section for the memo template: linkified prose paragraphs + figures."""
    citations = sec.get("citations", [])
    figures = []
    for f in sec.get("figures", []):
        fig = dict(f)
        ref = fig.get("image_ref", "")
        fig["image_url"] = _figure_url(data_dir, ref) if ref else ""
        figures.append(fig)
    return {
        "section_id": sec.get("section_id", ""),
        "title": sec.get("title", ""),
        "paragraphs": _linkify_prose(memo_id, sec.get("prose", ""), citations),
        "citations": citations,
        "figures": figures,
    }


def _figure_url(data_dir: str, image_ref: str) -> str:
    """Turn a figure's on-disk ``image_ref`` into a URL the browser can fetch.

    ``image_ref`` is a path to the file on disk (e.g. ``data/KYMR/figures/annotated/x.png``).
    We make it relative to the served data root, then point it at the figure route.
    """
    ref = image_ref.strip().lstrip("/")
    root = Path(data_dir).name
    if root and (ref == root or ref.startswith(root + "/")):
        ref = ref[len(root) + 1:]
    return url_for("figure", filename=ref)


_MARKER_SPLIT = re.compile(r"\n\s*\n")


def _linkify_prose(memo_id: str, prose: str, citations: List[dict]) -> List[Markup]:
    """Escape prose and turn each inline citation marker into a link to its audit view.

    Longer markers are replaced first so ``[1]`` never chews up part of ``[10]``.
    Returns one safe-HTML paragraph per blank-line-separated block.
    """
    ordered = sorted(
        (c for c in citations if c.get("marker")),
        key=lambda c: len(c["marker"]),
        reverse=True,
    )
    paragraphs: List[Markup] = []
    for block in _MARKER_SPLIT.split(prose.strip()):
        if not block.strip():
            continue
        html = str(escape(block))
        for c in ordered:
            marker = str(escape(c["marker"]))
            href = url_for(
                "audit", memo_id=memo_id, evidence_id=c.get("evidence_id", "")
            )
            label = str(escape(c.get("label") or c.get("evidence_id", "")))
            anchor = (
                f'<a class="cite" href="{href}" title="{label}">{marker}</a>'
            )
            html = html.replace(marker, anchor)
        paragraphs.append(Markup(html))
    return paragraphs


def _find_evidence(
    store: LedgerStore, memo_id: str, evidence_id: str
) -> Optional[Tuple[ClaimRecord, EvidenceRecord]]:
    """Locate an evidence row (and the claim it backs) by evidence id within a memo.

    The rendered artifact cites evidence by ``evidence_id``, but the ledger's
    ``get_evidence`` is keyed by ``claim_id`` — so we walk the memo's claims and match.
    """
    for claim in store.get_claims(memo_id):
        for ev in claim.evidence:
            if ev.evidence_id == evidence_id:
                return claim, ev
    return None
