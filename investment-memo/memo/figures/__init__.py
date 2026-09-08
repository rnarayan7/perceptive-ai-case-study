"""Figure insertion + annotation for the investment memo.

Takes figures from a company's (currently hand-seeded) figure manifest, matches each
memo claim to the figure that supports it, marks the feature the argument depends on
(arrow / box / callout) on the image, and returns ``report.Figure`` objects the
composition engine inserts at the claim.

Public surface:

- :func:`figures_for_claims` -- the interface the composition engine calls.
- :func:`annotate_image`, :class:`Annotation` -- the annotation engine.
- :func:`load_manifest`, :func:`resolve_manifest`, :class:`FigureManifest`,
  :class:`FigureRecord` -- the figure source.

Live IR/deck scraping is a documented follow-up (see ``docs/follow-ups.md`` and
``memo/figures/manifest.py``); this wave ships the engine, the matcher, and a seedable
manifest exercised end to end against bundled sample images.
"""

from __future__ import annotations

from memo.figures.annotate import Annotation, annotate_image, draw_arrow, draw_box, draw_callout
from memo.figures.manifest import (
    FigureManifest,
    FigureRecord,
    load_manifest,
    resolve_manifest,
)
from memo.figures.pipeline import best_figure, figures_for_claims, score

__all__ = [
    "Annotation",
    "annotate_image",
    "draw_arrow",
    "draw_box",
    "draw_callout",
    "FigureManifest",
    "FigureRecord",
    "load_manifest",
    "resolve_manifest",
    "best_figure",
    "figures_for_claims",
    "score",
]
