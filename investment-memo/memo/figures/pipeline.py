"""Claim -> figure matching and annotation.

The composition engine calls :func:`figures_for_claims`. For each claim it:

1. matches a figure from the company's manifest by keyword/asset-code overlap between
   the claim statement and the figure's keywords and caption,
2. annotates that image to mark the feature the claim depends on, and
3. returns a :class:`memo.report.render.Figure` pointing at the annotated image.

Matching is intentionally simple and deterministic (no model, no network): token
overlap with asset codes normalized so ``KT-621`` and ``KT621`` match. When nothing
clears the threshold for a claim, that claim contributes no figure; when nothing matches
at all the function returns ``[]`` rather than raising.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from PIL import Image

from memo.figures.annotate import annotate_image
from memo.figures.manifest import FigureRecord, resolve_manifest
from memo.report.render import Figure

# A keyword hit is worth more than a caption hit: keywords are the curated match terms.
_KEYWORD_WEIGHT = 3
_CAPTION_WEIGHT = 1
# Require at least one curated keyword to overlap before we call it a match.
_MIN_SCORE = _KEYWORD_WEIGHT

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]*")


def _tokens(text: str) -> set:
    """Lowercase tokens, plus a de-hyphenated variant so ``kt-621`` ~ ``kt621``."""
    out: set = set()
    for tok in _TOKEN.findall((text or "").lower()):
        tok = tok.strip("-")
        if not tok:
            continue
        out.add(tok)
        if "-" in tok:
            out.add(tok.replace("-", ""))
    return out


def _claim_id(claim: Any) -> Optional[str]:
    """Best-effort stable id for a claim (an explicit id, else None)."""
    for attr in ("id", "claim_id"):
        value = getattr(claim, attr, None)
        if value:
            return str(value)
    return None


def _claim_text(claim: Any) -> str:
    parts = [getattr(claim, "statement", "") or ""]
    value = getattr(claim, "value", None)
    if value:
        parts.append(str(value))
    return " ".join(parts)


def score(claim: Any, figure: FigureRecord) -> int:
    """Overlap score between a claim and a figure. Higher is a better match."""
    claim_tokens = _tokens(_claim_text(claim))
    if not claim_tokens:
        return 0
    keyword_tokens = set()
    for kw in figure.keywords:
        keyword_tokens |= _tokens(kw)
    caption_tokens = _tokens(figure.caption)
    kw_hits = len(claim_tokens & keyword_tokens)
    cap_hits = len(claim_tokens & caption_tokens)
    return kw_hits * _KEYWORD_WEIGHT + cap_hits * _CAPTION_WEIGHT


def best_figure(claim: Any, figures: List[FigureRecord]) -> Optional[FigureRecord]:
    """The best-scoring figure for a claim, or None if none clears the threshold."""
    best: Optional[FigureRecord] = None
    best_score = 0
    for figure in figures:
        s = score(claim, figure)
        if s > best_score:
            best, best_score = figure, s
    if best is None or best_score < _MIN_SCORE:
        return None
    return best


def _output_dir(company: str, storage: Any) -> Path:
    """Where annotated images are written: under storage when given, else a temp dir."""
    if storage is not None and getattr(storage, "root", None) is not None:
        out = Path(storage.root) / company / "figures" / "annotated"
    else:
        out = Path(tempfile.gettempdir()) / "memo_figures" / company
    out.mkdir(parents=True, exist_ok=True)
    return out


def figures_for_claims(company: str, claims: List[Any], storage: Any = None) -> List[Figure]:
    """Return annotated :class:`Figure` objects for the claims that match a figure.

    For each claim, the best-matching manifest figure (if any) is annotated to mark the
    relevant feature and returned as ``Figure(figure_id, caption, image_ref, claim_id)``.
    Returns ``[]`` when there is no manifest or nothing matches. Never raises on a miss.
    """
    manifest = resolve_manifest(company, storage=storage)
    if manifest is None or not len(manifest):
        return []

    figures = list(manifest.figures)
    out_dir = _output_dir(company, storage)
    results: List[Figure] = []

    for index, claim in enumerate(claims or []):
        match = best_figure(claim, figures)
        if match is None:
            continue
        if not match.image_path or not Path(match.image_path).exists():
            # A manifest can reference an image we do not have on disk yet; skip it
            # rather than fail the whole run.
            continue

        try:
            with Image.open(match.image_path) as img:
                size = img.size
        except Exception:  # noqa: BLE001 - unreadable image, skip this figure
            continue

        annotation = match.to_annotation(image_size=size)
        stem = f"{match.figure_id}_c{index}"
        out_path = out_dir / f"{stem}.png"
        image_ref = annotate_image(match.image_path, out_path, [annotation])

        results.append(Figure(
            figure_id=match.figure_id,
            caption=match.caption,
            image_ref=image_ref,
            claim_id=_claim_id(claim),
        ))

    return results
