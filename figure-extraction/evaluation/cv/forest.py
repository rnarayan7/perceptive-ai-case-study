"""Forest plot marker and confidence-interval measurement.

A forest plot stacks one row per subgroup: a marker (diamond or square) sits at
the point estimate (a hazard ratio), and a horizontal whisker spans the 95% CI
from a left x to a right x. The x-axis is linear, with a reference line at x=1;
whether a row's CI crosses 1 is the read that decides significance, so it is the
quantity worth measuring by pixel rather than eyeballing.

Given a row's y pixel band, the marker is the densest vertical cluster of the
marker color (the whisker is a thin line, the marker a taller blob, so counting
matches per column separates them), and its horizontal center is the point
estimate. The whisker's leftmost and rightmost matching columns in the band give
the CI bounds. Marker and whisker are often one color; pass ``whisker_color`` only
when they differ.

Like the waterfall and KM modules, this is validated on a synthetic plot with
known hazard ratios and known CI bounds; a real slide needs its own calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from evaluation.cv.image import RGB, Image


@dataclass
class Row:
    """One measured forest row: pixel positions and their data values."""

    label: str
    marker_px: float
    ci_low_px: int
    ci_high_px: int
    point_value: float
    ci_low: float
    ci_high: float


def measure_forest(
    image: Image,
    marker_color: RGB,
    tol: int,
    x_axis: object,
    rows_y: Sequence[Tuple[str, int, int]],
    x_range: "tuple[int, int]",
    whisker_color: Optional[RGB] = None,
    marker_frac: float = 0.5,
    min_marker_count: int = 2,
) -> List[Row]:
    """Measure the point estimate and CI of each forest row.

    ``rows_y`` is one ``(label, y0, y1)`` per row, giving the y pixel band to scan
    (``y1`` exclusive). For each band the marker is found as the columns whose
    count of ``marker_color`` matches is at least ``marker_frac`` of the band's
    peak count (and at least ``min_marker_count``): a diamond or square is taller
    than the thin whisker, so those columns bracket the marker, and their
    count-weighted center is the point estimate. The whisker extent is the
    leftmost and rightmost columns matching ``whisker_color`` (or ``marker_color``
    when the whisker shares the marker's color), giving the CI bounds. Rows with
    no marker color in the band are skipped.
    """
    x0, x1 = x_range
    w_color = whisker_color if whisker_color is not None else marker_color
    rows: List[Row] = []
    for label, y0, y1 in rows_y:
        counts = [(x, len(image.column_matches(x, marker_color, tol, y0, y1)))
                  for x in range(x0, x1)]
        present = [(x, c) for x, c in counts if c > 0]
        if not present:
            continue
        peak = max(c for _, c in present)
        thresh = max(min_marker_count, peak * marker_frac)
        marker_cols = [(x, c) for x, c in present if c >= thresh]
        if not marker_cols:
            marker_cols = present  # fall back to any match if none clear the bar
        weight = sum(c for _, c in marker_cols)
        marker_px = sum(x * c for x, c in marker_cols) / weight

        # Whisker extent: leftmost/rightmost column matching the whisker color.
        ci_low_px, ci_high_px = _whisker_extent(image, w_color, tol, x0, x1, y0, y1)
        if ci_low_px is None:  # no separate whisker: fall back to marker matches
            ci_low_px = min(x for x, _ in present)
            ci_high_px = max(x for x, _ in present)

        point_value = x_axis.to_value(marker_px)
        ci_low = x_axis.to_value(ci_low_px)
        ci_high = x_axis.to_value(ci_high_px)
        if ci_low > ci_high:  # a descending axis flips pixel order; keep low<=high
            ci_low, ci_high = ci_high, ci_low
        rows.append(Row(
            label=label, marker_px=marker_px,
            ci_low_px=ci_low_px, ci_high_px=ci_high_px,
            point_value=point_value, ci_low=ci_low, ci_high=ci_high,
        ))
    return rows


def _whisker_extent(image, color, tol, x0, x1, y0, y1):
    """Leftmost and rightmost columns in the band with a matching pixel."""
    lo = hi = None
    for x in range(x0, x1):
        if image.column_matches(x, color, tol, y0, y1):
            if lo is None:
                lo = x
            hi = x
    return lo, hi


# --- derived quantities ----------------------------------------------------

def crosses_one(rows: List[Row], reference: float = 1.0) -> List[str]:
    """Labels of rows whose CI spans the reference line (1.0 by default).

    A CI that includes 1 is the non-significant result, so these are the rows
    whose effect is not distinguishable from no effect.
    """
    return [r.label for r in rows if r.ci_low <= reference <= r.ci_high]


def significant(rows: List[Row], reference: float = 1.0) -> List[str]:
    """Labels of rows whose CI excludes the reference line (the significant ones)."""
    return [r.label for r in rows if not (r.ci_low <= reference <= r.ci_high)]
