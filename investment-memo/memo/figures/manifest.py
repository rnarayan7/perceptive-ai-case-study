"""Figure source: a seedable per-company manifest of figures.

Live IR/deck scraping is the highest-risk ingestion slice (per-site scraping plus
PDF/image extraction, rated Hard in ``docs/source-ingestion-feasibility.md``) and is a
documented follow-up, not built here. In its place a company's figures are declared in a
small JSON **manifest** that anyone can hand-seed:

    {
      "company": "KYMR",
      "figures": [
        {
          "figure_id": "kt621-stat6-pd",
          "image": "kt621_stat6_pd.png",          // relative to the manifest file
          "caption": "KT-621 drives dose-dependent STAT6 degradation ...",
          "source_url": "https://investors.kymeratx.com/...",
          "keywords": ["KT-621", "STAT6", "degradation", "biomarker"],
          "region": {                               // optional: what to mark, and how
            "kind": "box",
            "coords": [x0, y0, x1, y1],
            "text": "94% knockdown"
          }
        }
      ]
    }

The loader resolves each ``image`` path relative to the manifest so a manifest and its
images travel together. When a figure declares a ``region`` it also declares *where the
argument lives on the image*, which is what the annotation engine marks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from memo.figures.annotate import Annotation


@dataclass
class FigureRecord:
    """One registered figure and, optionally, the region its claim depends on."""

    figure_id: str
    image_path: Path  # absolute, resolved against the manifest location
    caption: str = ""
    source_url: str = ""
    keywords: List[str] = field(default_factory=list)
    region: Optional[Dict[str, Any]] = None  # {kind, coords, text?}

    def default_region(self) -> Dict[str, Any]:
        """The region to mark, defaulting to a box around the image's central area.

        A seeded figure should carry an explicit ``region``; when it does not we still
        mark *something* (a centered box) so the "figure is annotated" contract holds
        rather than inserting a bare image.
        """
        if self.region:
            return self.region
        return {"kind": "box", "coords": None, "text": ""}

    def to_annotation(self, image_size: Optional[tuple] = None) -> Annotation:
        """Build the :class:`Annotation` to draw for this figure.

        ``image_size`` (width, height) is used only to place the fallback centered box
        when the figure declared no explicit region.
        """
        region = self.default_region()
        kind = str(region.get("kind", "box")).lower()
        coords = region.get("coords")
        text = region.get("text", "") or ""
        point_to = region.get("point_to")
        if point_to is not None:
            point_to = tuple(point_to)

        if coords is None:
            w, h = image_size or (400, 300)
            if kind == "callout":
                coords = [int(w * 0.05), int(h * 0.05)]
            else:
                coords = [int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)]
        return Annotation(kind=kind, coords=coords, text=text, point_to=point_to)


@dataclass
class FigureManifest:
    """A company's registered figures, loaded from a JSON manifest."""

    company: str
    figures: List[FigureRecord] = field(default_factory=list)
    source_path: Optional[Path] = None

    def __iter__(self):
        return iter(self.figures)

    def __len__(self) -> int:
        return len(self.figures)


def load_manifest(path: Path) -> FigureManifest:
    """Load a figure manifest from ``path``, resolving image paths against its directory."""
    path = Path(path)
    data = json.loads(path.read_text())
    base = path.parent
    figures: List[FigureRecord] = []
    for entry in data.get("figures", []):
        image = entry.get("image", "")
        image_path = (base / image).resolve() if image else Path()
        figures.append(FigureRecord(
            figure_id=str(entry.get("figure_id", "")),
            image_path=image_path,
            caption=entry.get("caption", ""),
            source_url=entry.get("source_url", ""),
            keywords=list(entry.get("keywords", [])),
            region=entry.get("region"),
        ))
    return FigureManifest(
        company=str(data.get("company", "")),
        figures=figures,
        source_path=path,
    )


def resolve_manifest(company: str, storage: Any = None) -> Optional[FigureManifest]:
    """Find a seeded figure manifest for ``company`` under the data root, or ``None``.

    Looks for ``<storage.root>/<company>/figures/manifest.json`` (the manifest a
    figure-ingestion step writes). Returns ``None`` when it does not exist, so callers
    degrade to "no figures".
    """
    if storage is None or getattr(storage, "root", None) is None:
        return None
    path = Path(storage.root) / company / "figures" / "manifest.json"
    return load_manifest(path) if path.exists() else None
