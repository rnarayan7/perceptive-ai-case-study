"""Figure image serving (spec: annotated figures rendered inline in the memo).

Memo sections carry figures whose ``image_ref`` looks like
``data/<COMPANY>/figures/annotated/<name>.png`` (relative to the data root's parent).
This router maps that ref to the PNG on disk under ``DATA_ROOT/<COMPANY>/figures/`` and
serves it, refusing anything that resolves outside that company's figures directory.
"""

from __future__ import annotations

import mimetypes
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.deps import DATA_ROOT, get_ledger
from app import figures_data

router = APIRouter(prefix="/api", tags=["figures"])


@router.get("/figures")
def figures_list(
    company: Optional[str] = None,
    origin: Optional[str] = None,
    ledger=Depends(get_ledger),
):
    """List the figure corpus (optionally filtered to one company or one origin)."""
    return figures_data.list_figures(ledger, company=company, origin=origin)


# figure-extraction images (Stage-1 brief + harvested FDA/PMC corpus). Declared BEFORE the
# generic company route so "fx" is not swallowed as a company segment. Only files under the
# allowlisted bases are served (path-traversal guard, same shape as the company route).
_FX_ROOT = figures_data.FX_ROOT
_FX_ALLOWED = [
    (_FX_ROOT / "assets" / "figures").resolve(),
    (_FX_ROOT / "data" / "corpus").resolve(),
]


@router.get("/figures/fx/{path:path}")
def figure_fx(path: str):
    """Serve one figure-extraction image, resolved under an allowlist of FX subtrees."""
    target = (_FX_ROOT / path).resolve()
    if not any(base == target or base in target.parents for base in _FX_ALLOWED):
        raise HTTPException(404, "figure not found")
    if not target.is_file():
        raise HTTPException(404, "figure not found")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@router.get("/figures/{company}/{ref:path}")
def figure(company: str, ref: str):
    """Serve one annotated figure PNG for a company.

    ``ref`` is normally the path under the company's figures dir (e.g.
    ``annotated/<name>.png``). A redundant ``.../figures/`` prefix left on by the
    caller is tolerated. Only files that resolve inside ``DATA_ROOT/<company>/figures``
    are served (path-traversal guard).
    """
    data_root = DATA_ROOT.resolve()
    base = (DATA_ROOT / company / "figures").resolve()
    # Reject a traversal-laden company that escapes the data root.
    if data_root not in base.parents:
        raise HTTPException(404, "figure not found")

    # Tolerate a caller that passes the full image_ref (".../figures/annotated/x.png").
    if "figures/" in ref:
        ref = ref.split("figures/", 1)[1]

    target = (base / ref).resolve()
    if base not in target.parents:
        raise HTTPException(404, "figure not found")
    if not target.is_file():
        raise HTTPException(404, "figure not found")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)
