"""PK / concentration-time curve tracing and read-off (log y-axis).

A pharmacokinetic figure plots concentration against time: x is linear (days),
y is LOGARITHMIC (concentration, e.g. nM over several decades). Each dose level
is one colored curve through sampled points, generally decaying over time. The
headline reads are geometric: the concentration at a given day, and the fold
change between two days (or against a marked threshold line).

Tracing mirrors the Kaplan-Meier module. For each column the rows matching a
curve's color are the line plus any marker dot sharing that column; the
representative row is the one nearest the previous column's y, which walks along
the line and steps past a fat marker instead of jumping to its edge. From the
traced curve a concentration is a y read at a target x, converted through the
LOG y-axis, and a fold multiple is the ratio of two such reads.

Because the y-axis is logarithmic, one pixel of slop is not a fixed error but a
FOLD: ``LogAxis.fold_per_pixel()`` is the multiplicative uncertainty on every
read here, so a concentration is only good to that factor and a fold multiple
carries it on both ends. Validated on synthetic curves with known
concentrations at known days; a real figure needs its own axis calibration.
"""

from __future__ import annotations

from typing import Dict, Optional

from evaluation.cv.image import RGB, Image


def trace_curve(
    image: Image,
    curve_color: RGB,
    tol: int,
    x_range: "tuple[int, int]",
    y_range: "tuple[int, int]",
) -> Dict[int, int]:
    """Trace one dose curve to a {column x -> representative row y} mapping.

    For each column, the rows matching ``curve_color`` are the line plus any
    marker dot centered on that column. The representative is the matching row
    nearest the previous column's y, which stays on the line and steps past a
    fat marker (a dot spans several rows) rather than jumping to its edge. The
    first present column is seeded with the median of its matches, a stable
    center for the leftmost marker. Columns with no match are omitted.
    """
    x0, x1 = x_range
    y0, y1 = y_range
    curve: Dict[int, int] = {}
    prev_y: Optional[int] = None
    for x in range(x0, x1):
        rows = image.column_matches(x, curve_color, tol, y0, y1)
        if not rows:
            continue
        if prev_y is None:
            y = sorted(rows)[len(rows) // 2]  # seed on the marker's center
        else:
            y = min(rows, key=lambda r: abs(r - prev_y))
        curve[x] = y
        prev_y = y
    return curve


def concentration_at(
    curve: Dict[int, int],
    x_axis: object,
    y_axis: object,
    x_target: float,
) -> Optional[float]:
    """The concentration at time ``x_target``, read off the traced curve.

    Maps ``x_target`` to a pixel column, takes the curve's y at (or nearest to)
    that column, and converts it through the LOG ``y_axis`` to a concentration.
    Returns ``None`` if the curve is empty. The read is good to
    ``y_axis.fold_per_pixel()`` (a fold), since one pixel of y is one fold-step.
    """
    if not curve:
        return None
    px_target = x_axis.to_pixel(x_target)
    xs = sorted(curve)
    if px_target <= xs[0]:
        py = curve[xs[0]]
    elif px_target >= xs[-1]:
        py = curve[xs[-1]]
    else:
        nearest = min(xs, key=lambda x: abs(x - px_target))
        py = curve[nearest]
    return y_axis.to_value(py)


def fold_multiple(
    curve: Dict[int, int],
    x_axis: object,
    y_axis: object,
    x_a: float,
    x_b: float,
) -> Optional[float]:
    """The fold change in concentration from time ``x_b`` to time ``x_a``.

    A dimensionless ratio ``concentration_at(x_a) / concentration_at(x_b)`` (so
    x_a earlier than x_b on a decaying curve gives a fold > 1). Returns ``None``
    if the curve is empty or the ``x_b`` concentration reads as zero.
    """
    ca = concentration_at(curve, x_axis, y_axis, x_a)
    cb = concentration_at(curve, x_axis, y_axis, x_b)
    if ca is None or cb is None or cb == 0:
        return None
    return ca / cb


def threshold_fold(peak_conc: float, threshold_conc: float) -> Optional[float]:
    """How many folds a peak concentration sits above a marked threshold.

    ``peak_conc / threshold_conc``: e.g. a peak 8x over a 1 nM efficacy line.
    Returns ``None`` if the threshold is zero.
    """
    if not threshold_conc:
        return None
    return peak_conc / threshold_conc
