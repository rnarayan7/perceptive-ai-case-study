"""Read-side listing of the figure corpus for the Data > Figures view.

Three groups are surfaced, one row per figure:

1. company - the coverage-universe memo figures ingested into
   ``data/<COMPANY>/figures/`` (a manifest plus annotated PNGs). ``company`` is the
   ticker; these carry a ``cited`` flag marking what the latest memo inserts.
2. stage1 - the six Stage-1 brief slides under
   ``figure-extraction/assets/figures/``. Captions/provenance come from
   ``evaluation/gold/reference_figures.json``; the named company (Inhibrx, Janux, ...)
   is the identifier since there is no ticker.
3. corpus - the ~409 harvested FDA/PMC figures under
   ``figure-extraction/data/corpus/{fda,pmc}/<id>/`` (an image + ``record.json``).

The stage1 + corpus enumeration is static on disk (harvested offline), so it is read
once and cached at module level, like ``documents.py`` caches its corpus. Image bytes
are served separately by the figures router; each row carries an ``image_url`` (an
absolute API path) the frontend fetches directly, so it never needs the disk roots.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from memo.compose.engine import load_artifact
from memo.figures.manifest import resolve_manifest
from memo.ingestion.base import Storage

from app.deps import DATA_ROOT
from app.registry import ORDER

# figure-extraction is a sibling of investment-memo (DATA_ROOT is investment-memo/data).
FX_ROOT = (DATA_ROOT.parent.parent / "figure-extraction").resolve()
_CORPUS_ROOT = FX_ROOT / "data" / "corpus"
_STAGE1_ASSETS = FX_ROOT / "assets" / "figures"
_REFERENCE_JSON = FX_ROOT / "evaluation" / "gold" / "reference_figures.json"

# Curated company/caption for the six Stage-1 brief figures. figure_type + image path are
# read from reference_figures.json; the company label and a plain-language caption are
# derived from the provenance recorded there (some slides name no company - labelled
# "Stage-1 brief"). Nothing here is invented beyond what the figure genuinely shows.
_STAGE1_META = {
    "fig01_km":        ("Inhibrx",       "Kaplan-Meier PFS: ozekibart vs placebo"),
    "fig02_waterfall": ("Janux",         "Waterfall of PSA and RECIST response"),
    "fig03_pk":        ("Stage-1 brief", "Dose-level PK on a log-scale concentration axis"),
    "fig04_forest":    ("Stage-1 brief", "Forest plot of hazard ratios by subgroup"),
    "fig05_table":     ("CG Oncology",   "Complete-response rates across bladder-cancer programs"),
    "fig06_spider":    ("Immatics",      "Spider plot of tumor trajectories over time"),
}

# Cache the static stage1 + corpus rows once (enumerated offline; restart to pick up a
# re-harvest). Company rows stay live since the memo ledger drives their ``cited`` flag.
_STATIC_CACHE: Optional[List[Dict[str, Any]]] = None
_LOCK = threading.Lock()

_GENERIC_TITLE = re.compile(r"figure\s+\d+\s*$", re.IGNORECASE)
_BRAND = re.compile(r"([A-Z][A-Za-z]{2,})\s*\(([a-z][a-z\- ]{2,})\)")
_REG_ID = re.compile(r"\b(nda|bla)[_ ]?(\d{5,6})\b", re.IGNORECASE)


def _cited_figure_ids(ledger, company: str, storage: Storage) -> set:
    """figure_ids the company's latest memo inserts (best-effort, never raises)."""
    try:
        memo = ledger.latest_memo(company)
        if memo is None:
            return set()
        artifact = load_artifact(memo.memo_id, storage)
        return {
            f["figure_id"]
            for s in artifact.get("sections", [])
            for f in s.get("figures", [])
        }
    except Exception:  # noqa: BLE001 - a missing/unreadable artifact just means "unknown"
        return set()


def _company_rows(ledger, want: Optional[str]) -> List[Dict[str, Any]]:
    """Coverage-universe memo figures, one row per annotated PNG (optionally one ticker)."""
    storage = Storage(root=DATA_ROOT)
    rows: List[Dict[str, Any]] = []
    for co in ORDER:
        if want and co != want:
            continue
        manifest = resolve_manifest(co, storage=storage)
        if manifest is None:
            continue
        cited = _cited_figure_ids(ledger, co, storage)
        for fig in manifest.figures:
            rows.append({
                "origin": "company",
                "company": co,
                "figure_id": fig.figure_id,
                "caption": fig.caption or "",
                "figure_type": "",
                "source": "company",
                "source_url": getattr(fig, "source_url", None),
                "image_ref": f"data/{co}/figures/annotated/{fig.figure_id}.png",
                "image_url": f"/api/figures/{co}/annotated/{fig.figure_id}.png",
                "cited": fig.figure_id in cited,
            })
    return rows


def _first_line(text: str, limit: int = 200) -> str:
    """First non-empty line of ``text``, whitespace-collapsed and length-capped."""
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if line:
            return line[:limit].rstrip()
    return ""


def _fda_label(doc_label: str, title: str, context: str) -> str:
    """A short source/drug label for an FDA figure: brand name or NDA/BLA id."""
    brand_match = _BRAND.search(title) or _BRAND.search(context)
    if brand_match:
        return brand_match.group(1)
    reg = _REG_ID.search(doc_label) or _REG_ID.search(title)
    if reg:
        return f"FDA {reg.group(1).upper()} {reg.group(2)}"
    return "FDA review"


def _corpus_caption(rec: Dict[str, Any], doc_label: str) -> str:
    """caption -> title (unless a generic auto-title) -> a trimmed context line."""
    cap = (rec.get("caption") or "").strip()
    if cap:
        return cap
    title = (rec.get("title") or "").strip()
    if title and not _GENERIC_TITLE.search(title):
        return title
    ctx = _first_line(rec.get("context") or "")
    if ctx:
        return ctx
    return title


def _corpus_rows() -> List[Dict[str, Any]]:
    """Every harvested FDA/PMC figure with a readable record.json, source then id."""
    rows: List[Dict[str, Any]] = []
    for source in ("fda", "pmc"):
        base = _CORPUS_ROOT / source
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            record = folder / "record.json"
            if not record.is_file():
                continue
            try:
                rec = json.loads(record.read_text())
            except Exception:  # noqa: BLE001 - skip an unreadable record
                continue
            figure_id = rec.get("figure_id") or folder.name
            image_file = rec.get("image_file") or "image.png"
            if not (folder / image_file).is_file():
                continue
            meta = rec.get("metadata") or {}
            doc_label = str(meta.get("doc_label") or "")
            if source == "fda":
                label = _fda_label(doc_label, rec.get("title") or "", rec.get("context") or "")
            else:
                journal = str(meta.get("journal") or "").split(" : ")[0].strip()
                label = journal[:44] if journal else "PMC"
            rows.append({
                "origin": "corpus",
                "company": label,
                "figure_id": figure_id,
                "caption": _corpus_caption(rec, doc_label),
                "figure_type": rec.get("figure_type") or "",
                "source": source,
                "source_url": rec.get("url") or None,
                "image_ref": None,
                "image_url": f"/api/figures/fx/data/corpus/{source}/{figure_id}/{image_file}",
                "cited": False,
            })
    return rows


def _stage1_rows() -> List[Dict[str, Any]]:
    """The six Stage-1 brief figures from reference_figures.json + curated labels."""
    if not _REFERENCE_JSON.is_file():
        return []
    try:
        ref = json.loads(_REFERENCE_JSON.read_text())
    except Exception:  # noqa: BLE001
        return []
    rows: List[Dict[str, Any]] = []
    for key, (label, caption) in _STAGE1_META.items():
        entry = ref.get(key)
        if not isinstance(entry, dict):
            continue
        image = entry.get("image") or ""
        if not image or not (FX_ROOT / image).is_file():
            continue
        rows.append({
            "origin": "stage1",
            "company": label,
            "figure_id": key,
            "caption": caption,
            "figure_type": entry.get("figure_type") or "",
            "source": "stage1",
            "source_url": None,
            "image_ref": None,
            "image_url": f"/api/figures/fx/{image}",
            "cited": False,
        })
    return rows


def _static_rows() -> List[Dict[str, Any]]:
    """Cached stage1 + corpus rows (static on disk)."""
    global _STATIC_CACHE
    if _STATIC_CACHE is None:
        with _LOCK:
            if _STATIC_CACHE is None:
                _STATIC_CACHE = _stage1_rows() + _corpus_rows()
    return _STATIC_CACHE


def list_figures(
    ledger,
    company: Optional[str] = None,
    origin: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Every figure across the three groups.

    ``company`` filters the company group to one ticker (stage1/corpus have no ticker, so
    they drop out when a ticker filter is set). ``origin`` filters by group.
    """
    want = company.upper() if company else None
    origin_f = origin.lower() if origin else None

    rows: List[Dict[str, Any]] = []
    if origin_f in (None, "company"):
        rows.extend(_company_rows(ledger, want))
    if not want:
        for row in _static_rows():
            if origin_f in (None, row["origin"]):
                rows.append(row)
    return rows
