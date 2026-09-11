"""Waterfall bar measurement.

The figure type where eyeballing fails worst (my manual PSA90 read was 10% vs a
true 26%), so it is the first fully-worked CV read. Given the bar color of the
subgroup of interest, the plot region, the y=0 baseline row, and a y-axis
calibration, it segments the bars and measures each bar's extreme value.

Selecting the bar color is also how the denominator is controlled: the >=2 mg
subgroup is one color, so counting only those bars answers "proportion of the
>=2 mg subgroup" over the right denominator, the trap the brief set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from evaluation.cv.image import RGB, Image


@dataclass
class Bar:
    """One measured bar: its column span, center, and signed value."""

    x_start: int
    x_end: int
    extreme_py: int
    value: float

    @property
    def x_center(self) -> float:
        return (self.x_start + self.x_end) / 2


def measure_waterfall(
    image: Image,
    bar_color: RGB,
    baseline_py: int,
    y_axis: object,
    x_range: "tuple[int, int]",
    y_range: "tuple[int, int]",
    tol: int = 40,
    min_run_px: int = 3,
    max_gap_px: int = 2,
    baseline_band: int = 0,
    baseline_tol: Optional[int] = None,
) -> List[Bar]:
    """Segment bars of ``bar_color`` and measure each one's extreme value.

    A bar is a run of consecutive columns that contain the bar color; small gaps
    (<= ``max_gap_px``) are bridged so a bar split by an anti-aliased edge stays
    one bar. Each bar's value is the data value at its row furthest from the
    baseline (the tip of the bar).

    Near-zero bars are only a pixel or two tall, sit against the axis line, and
    are heavily anti-aliased, so the main ``tol`` misses them. Set
    ``baseline_band`` (rows either side of the baseline) with a relaxed
    ``baseline_tol`` to recover them: a column with no full-height match is
    rescanned in that thin band with the looser tolerance. Left off (0) by
    default so panels that do not need it are unchanged.
    """
    x0, x1 = x_range
    y0, y1 = y_range
    b_tol = tol if baseline_tol is None else baseline_tol
    # Per-column: does this column contain the bar color, and its extreme row.
    col_extreme: List[Optional[int]] = []
    for x in range(x0, x1):
        rows = image.column_matches(x, bar_color, tol, y0, y1)
        if not rows and baseline_band:
            rows = [y for y in range(baseline_py - baseline_band, baseline_py + baseline_band + 1)
                    if y != baseline_py and image.matches(image.pixel(x, y), bar_color, b_tol)]
        if not rows:
            col_extreme.append(None)
            continue
        # The tip is the matching row furthest from the baseline.
        col_extreme.append(max(rows, key=lambda y: abs(y - baseline_py)))

    bars: List[Bar] = []
    run_start: Optional[int] = None
    gap = 0
    for idx, extreme in enumerate(col_extreme):
        if extreme is not None:
            if run_start is None:
                run_start = idx
            gap = 0
        else:
            if run_start is not None:
                gap += 1
                if gap > max_gap_px:
                    _close_bar(bars, col_extreme, run_start, idx - gap, x0, y_axis, min_run_px, baseline_py)
                    run_start = None
                    gap = 0
    if run_start is not None:
        _close_bar(bars, col_extreme, run_start, len(col_extreme) - 1, x0, y_axis, min_run_px, baseline_py)
    return bars


def _close_bar(bars, col_extreme, start_idx, end_idx, x0, y_axis, min_run_px, baseline_py) -> None:
    if end_idx - start_idx + 1 < min_run_px:
        return
    extremes = [col_extreme[i] for i in range(start_idx, end_idx + 1) if col_extreme[i] is not None]
    if not extremes:
        return
    tip_py = max(extremes, key=lambda y: abs(y - baseline_py))
    value = y_axis.to_value(tip_py)
    bars.append(Bar(x_start=x0 + start_idx, x_end=x0 + end_idx, extreme_py=tip_py, value=value))


# --- derived quantities ----------------------------------------------------

def proportion_beyond(bars: List[Bar], threshold: float, below: bool = True) -> "tuple[int, int]":
    """Count bars past a threshold. ``below=True`` counts value <= threshold
    (for reductions); returns (count, total) so the denominator is explicit."""
    total = len(bars)
    if below:
        count = sum(1 for b in bars if b.value <= threshold)
    else:
        count = sum(1 for b in bars if b.value >= threshold)
    return count, total


def deepest(bars: List[Bar]) -> Optional[float]:
    return min((b.value for b in bars), default=None)


def leftmost(bars: List[Bar]) -> Optional[Bar]:
    return min(bars, key=lambda b: b.x_start) if bars else None
