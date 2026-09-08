"""Kaplan-Meier curve tracing and read-off.

The KM figure is where the two headline numbers live: median survival (the time
the curve crosses 0.5) and a landmark probability (survival at a fixed month).
Both are geometric, so eyeballing them off a stepped curve is exactly the kind of
read pixel measurement does better.

The curve is a descending step function drawn in one color per arm. Two wrinkles
make it more than "read the line": censoring tick marks add short vertical marks
on the line, and a near-vertical drop puts many rows in one column. Tracing keeps
to the line by following the previous column's y, which walks past ticks and, at a
drop, hands off cleanly to the next segment. From the traced curve the median is
an interpolated x-crossing and a landmark is a y read at a target x.

Like the waterfall module, this is validated on synthetic curves with a known
median and known landmark value; a real slide needs its own axis calibration.
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
    """Trace one arm's curve to a {column x -> representative row y} mapping.

    For each column, the rows matching ``curve_color`` are the line plus any
    censoring tick sharing that column. The representative is the matching row
    nearest the previous column's y, which stays on the horizontal segment
    (ignoring a tick that straddles it) and, at a vertical drop, keeps the upper
    level for that column before handing off to the lower segment in the next.
    The first present column is seeded with its topmost match, since a survival
    curve starts high. Columns with no match are omitted.
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
            y = min(rows)  # start of the curve: the highest (topmost) point
        else:
            y = min(rows, key=lambda r: abs(r - prev_y))
        curve[x] = y
        prev_y = y
    return curve


def median_crossing(
    curve: Dict[int, int],
    x_axis: object,
    y_axis: object,
    level: float = 0.5,
) -> Optional[float]:
    """The x-axis value where the curve first falls to ``level`` (0.5 = median).

    Walks columns left to right, converts each row to a survival value, and
    returns the x value the first time survival reaches ``level``, interpolating
    in x between the last column above the level and the first at or below it.
    Returns ``None`` if the curve never reaches ``level`` ("not reached").
    """
    prev = None  # (x_pixel, survival) of the last column above the level
    for x in sorted(curve):
        surv = y_axis.to_value(curve[x])
        if surv <= level:
            if prev is None:
                return x_axis.to_value(x)  # already at/below at the first column
            px, psurv = prev
            frac = 0.0 if psurv == surv else (psurv - level) / (psurv - surv)
            return x_axis.to_value(px + frac * (x - px))
        prev = (x, surv)
    return None


def value_at(
    curve: Dict[int, int],
    x_axis: object,
    y_axis: object,
    x_target: float,
) -> Optional[float]:
    """The survival probability at x-axis value ``x_target`` (a landmark read).

    Maps ``x_target`` to a pixel column and reads the curve there. On a vertical
    drop at that column the lower survival is taken (the right-continuous value,
    what "survival at month t" means). Returns ``None`` if the curve is empty.
    """
    if not curve:
        return None
    px_target = x_axis.to_pixel(x_target)
    xs = sorted(curve)
    if px_target <= xs[0]:
        return y_axis.to_value(curve[xs[0]])
    if px_target >= xs[-1]:
        return y_axis.to_value(curve[xs[-1]])
    # Nearest column; ties go to the larger row (lower survival, right-continuous).
    nearest = min(xs, key=lambda x: (abs(x - px_target), -curve[x]))
    return y_axis.to_value(curve[nearest])
