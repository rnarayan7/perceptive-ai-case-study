"""Per-figure-type readers over the shared primitives.

A ``Reader`` recovers a figure's structure, measures it, and declares the
invariants that say whether the result should be believed. Adding a figure type
means adding a reader and its invariants, not rebuilding the pipeline: the
primitives (:mod:`evaluation.cv.primitives`) and the verifier
(:mod:`evaluation.cv.verify`) are shared and type-agnostic.

The waterfall reader is the worked example. Its structure recovery is driven
entirely by properties true of every waterfall in any journal or template, so it
needs no per-figure configuration and no prior exposure to the style:

* bars are runs of near-constant height, so a *step* marks a bar boundary
  (survives dense panels where bars touch and gap-based splitting fuses them);
* a panel is sorted, so a *jump upward* marks the start of the next panel
  (survives abutting panels that whitespace-based splitting cannot separate);
* bars grow from zero, so a mark that never touches the baseline is a legend
  swatch and not data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from evaluation.cv.image import RGB, Image
from evaluation.cv.primitives import (
    column_tips,
    grid_segments,
    plateau_segments,
    split_on_monotonicity,
)
from evaluation.cv.verify import (
    Verdict,
    check_axis,
    check_count,
    check_resolution,
    check_sorted,
)
from evaluation.cv.waterfall import Bar


@dataclass
class Panel:
    """One measured panel: its column span, its bars, and its verdict."""

    x_start: int
    x_end: int
    bars: List[Bar] = field(default_factory=list)
    verdict: Optional[Verdict] = None

    @property
    def values(self) -> List[float]:
        return [b.value for b in self.bars]

    @property
    def width_per_bar(self) -> float:
        return (self.x_end - self.x_start) / max(1, len(self.bars))


class WaterfallReader:
    """Recover, measure and verify a waterfall figure."""

    figure_type = "waterfall"

    def __init__(self, image: Image, colors: Sequence[RGB], axis, baseline_py: int,
                 tol: int = 34, min_bar_px: Optional[int] = None,
                 step_tol: Optional[int] = None) -> None:
        self.image = image
        self.colors = list(colors)
        self.axis = axis
        self.baseline_py = baseline_py
        self.tol = tol
        # Both tolerances are in pixels, so both must scale with resolution. A
        # tip wobble that is 2px in a 750px rendition is ~6px in the 2400px
        # original; holding the tolerance fixed turns one bar into several as
        # soon as the image gets sharper, which is the opposite of the intent.
        self.min_bar_px = min_bar_px if min_bar_px is not None else max(2, image.width // 400)
        self.step_tol = step_tol if step_tol is not None else max(2, image.height // 200)

    # -- structure ---------------------------------------------------------

    def recover_structure(self, x_range: Tuple[int, int],
                          y_range: Tuple[int, int],
                          split_panels: bool = True) -> List[Panel]:
        """Segment bars by height plateau, optionally splitting panels on sort order.

        ``split_panels`` should be **False** whenever panel boundaries come from
        detection. The monotonicity split is a fallback for when nothing knows
        where the panels are; applied on top of a real detection it only
        fragments a correctly-identified panel on noisy tips.
        """
        x0, _ = x_range
        tips = column_tips(self.image, self.colors, self.baseline_py,
                           x_range, y_range, tol=self.tol)
        segments = plateau_segments(tips, min_width=self.min_bar_px,
                                    step_tol=self.step_tol)

        # Baseline contact is already enforced by ``column_tips`` (it only walks
        # marks contiguous with the zero line), so legend swatches never reach here.
        bars: List[Bar] = [
            Bar(x_start=x0 + s, x_end=x0 + e, extreme_py=tip,
                value=self.axis.to_value(tip))
            for s, e, tip in segments
        ]
        if not bars:
            return []

        if not split_panels:
            return [Panel(x_start=bars[0].x_start, x_end=bars[-1].x_end, bars=bars)]

        # Split into panels where the sorted order breaks (a jump back up).
        values = [b.value for b in bars]
        span = max(values) - min(values)
        jump_tol = max(15.0, 0.25 * span)
        starts = split_on_monotonicity(values, jump_tol=jump_tol)

        panels: List[Panel] = []
        bounds = [0] + starts + [len(bars)]
        for i in range(len(bounds) - 1):
            group = bars[bounds[i]:bounds[i + 1]]
            if not group:
                continue
            panels.append(Panel(x_start=group[0].x_start,
                                x_end=group[-1].x_end, bars=group))
        return panels

    def recover_grid(self, x_range: Tuple[int, int], y_range: Tuple[int, int],
                     n_bars: int) -> Tuple[Optional[Panel], float]:
        """Segment into exactly ``n_bars`` equal-width slots (see grid_segments).

        Returns the panel and the *fill rate*: the fraction of slots that
        actually contained a mark. Because N drives the segmentation here, N can
        no longer verify it, and the fill rate takes over as the independent
        check: a grid laid on the wrong span leaves empty slots.
        """
        x0, _ = x_range
        tips = column_tips(self.image, self.colors, self.baseline_py,
                           x_range, y_range, tol=self.tol)
        slots = grid_segments(tips, n_bars)
        if not slots:
            return None, 0.0
        bars: List[Bar] = []
        for slot in slots:
            if slot is None:
                continue
            s, e, tip = slot
            bars.append(Bar(x_start=x0 + s, x_end=x0 + e, extreme_py=tip,
                            value=self.axis.to_value(tip)))
        fill = len(bars) / float(n_bars)
        if not bars:
            return None, fill
        return Panel(x_start=bars[0].x_start, x_end=bars[-1].x_end, bars=bars), fill


    # -- verification ------------------------------------------------------

    def verify(self, panel: Panel, auto_axis=None,
               stated_n: Optional[int] = None) -> Verdict:
        """Run the reader's invariants over one measured panel."""
        # Sortedness is tested with the same slack that decides a panel break:
        # a rise too small to be a new panel is pixel noise, not a broken sort.
        values = panel.values
        span = (max(values) - min(values)) if values else 0.0
        slack = max(3.0, 0.05 * span)
        verdict = Verdict(checks=[
            check_axis(auto_axis),
            check_sorted(values, slack=slack),
            check_count(len(panel.bars), stated_n),
            check_resolution(panel.width_per_bar),
        ])
        panel.verdict = verdict
        return verdict
