"""Shared geometric primitives for chart readers.

These are the building blocks every figure-type reader composes, and they are
deliberately generic: nothing here knows what a waterfall or a forest plot is.

The important one is :func:`plateau_segments`. Segmenting bars by the *gaps*
between them fails on dense charts, where neighbouring bars touch and fuse. But
a sorted bar chart has a stronger structural signal: each bar is a run of columns
at a near-constant height, and a bar boundary is a *step* in that height. Reading
the step instead of the gap is what survives density.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from evaluation.cv.image import RGB, Image


def column_tips(image: Image, colors: Sequence[RGB], baseline_py: int,
                x_range: Tuple[int, int], y_range: Tuple[int, int],
                tol: int = 34, max_gap: Optional[int] = None) -> List[Optional[int]]:
    """Per column, the tip of the bar that grows *contiguously* from the baseline.

    ``None`` where the column has no mark touching the zero line. Matching against
    a list of colours unions a multi-series chart into one mask, which is what the
    count-style questions ("how many bars past -30%") need.

    Contiguity is the invariant that matters here. Taking simply the matching row
    furthest from the baseline breaks whenever another mark of the same colour
    sits in the same columns (a legend swatch under the plot is the common case):
    that mark is further away, so it is mistaken for the tip. Walking outward from
    the baseline and stopping where the colour stops means only marks physically
    connected to zero can be read as bars, which is true of every bar chart and
    false of every legend.

    ``max_gap`` must be wide enough to step over a dashed threshold line drawn
    *across* the bars, or every bar is truncated at the first reference line it
    crosses. It stays far smaller than the distance to a detached legend, so
    bridging line thickness does not reconnect unrelated marks.

    It defaults to a fraction of the image width because **rule thickness scales
    with resolution**: the same dashed line that is 2px wide in a 750px web
    rendition is ~8px in the 2400px print original, so a fixed tolerance silently
    truncates every positive bar at the first threshold once resolution improves.
    """
    x0, x1 = x_range
    y0, y1 = y_range
    if max_gap is None:
        max_gap = max(5, image.width // 200)

    def matches(x: int, y: int) -> bool:
        if y < y0 or y >= y1:
            return False
        px = image.pixel(x, y)
        return any(Image.matches(px, c, tol) for c in colors)

    def walk(x: int, step: int) -> int:
        """Furthest row reached from the baseline in direction ``step``."""
        best = baseline_py
        y = baseline_py
        gap = 0
        while True:
            y += step
            if y < y0 or y >= y1:
                break
            if matches(x, y):
                best = y
                gap = 0
            else:
                gap += 1
                if gap > max_gap:
                    break
        return best

    tips: List[Optional[int]] = []
    for x in range(x0, x1):
        # The walk does its own seeding: it starts at the zero line and tolerates
        # up to ``max_gap`` non-matching rows, so it reaches a negative bar that
        # begins just past a thick baseline rule. A column with no connected bar
        # never leaves the baseline in either direction.
        up, down = walk(x, -1), walk(x, +1)
        if up == baseline_py and down == baseline_py:
            tips.append(None)
            continue
        tips.append(up if abs(up - baseline_py) >= abs(down - baseline_py) else down)
    return tips


def plateau_segments(tips: Sequence[Optional[int]], min_width: int = 2,
                     step_tol: int = 2) -> List[Tuple[int, int, int]]:
    """Group columns into bars by near-constant tip height.

    A new bar starts when the tip steps by more than ``step_tol`` pixels, or when
    the mark disappears. Returns (start_idx, end_idx, tip_row) per bar, indices
    relative to ``tips``.

    This needs no whitespace between bars, which is the whole point: on a dense
    panel the bars touch, but because the chart is sorted their heights still
    differ, so the boundary is visible as a step even when the gap is not.
    """
    bars: List[Tuple[int, int, int]] = []
    start: Optional[int] = None
    ref: Optional[int] = None
    last: Optional[int] = None

    def close(end_idx: int) -> None:
        if start is None or ref is None:
            return
        if end_idx - start + 1 >= min_width:
            bars.append((start, end_idx, ref))

    for i, tip in enumerate(tips):
        if tip is None:
            close(i - 1)
            start = ref = last = None
            continue
        if start is None:
            start, ref, last = i, tip, tip
            continue
        if abs(tip - ref) > step_tol:
            close(i - 1)
            start, ref = i, tip
        last = tip
    if start is not None:
        close(len(tips) - 1)
    return bars


def grid_segments(tips: Sequence[Optional[int]], n_bars: int,
                  edge_frac: float = 0.2
                  ) -> List[Optional[Tuple[int, int, int]]]:
    """Slice the marked span into ``n_bars`` equal slots and take each slot's tip.

    Segmenting on tip *height* fails on a dense sorted chart for a structural
    reason: sorting makes neighbouring bars nearly the same height, so the
    tolerance that merges pixel noise within one bar also merges two real bars.
    Those two constraints overlap, so no tolerance works.

    Bars are equal width, though, and the figure prints its own N. Slicing the
    marked span into N equal slots therefore recovers exactly N bars no matter
    how similar their heights are. Each slot's tip is the *median* over its middle
    columns, which discards the anti-aliased edges and the tip wobble that
    otherwise splits one bar in two.

    Returns one entry per slot: ``(start_idx, end_idx, tip_row)``, or ``None``
    for a slot containing no mark (the caller uses the fill rate as a fit check,
    since N driving the segmentation means N can no longer verify it).
    """
    marked = [i for i, t in enumerate(tips) if t is not None]
    if not marked or n_bars <= 0:
        return []
    first, last = marked[0], marked[-1]
    pitch = (last - first + 1) / n_bars
    out: List[Optional[Tuple[int, int, int]]] = []
    for i in range(n_bars):
        s = first + i * pitch
        e = first + (i + 1) * pitch
        lo = max(first, int(round(s + edge_frac * pitch)))
        hi = min(last, max(lo, int(round(e - edge_frac * pitch)) - 1))
        vals = sorted(t for t in (tips[j] for j in range(lo, hi + 1)) if t is not None)
        if not vals:
            out.append(None)
            continue
        out.append((int(round(s)), max(int(round(s)), int(round(e)) - 1),
                    vals[len(vals) // 2]))
    return out


def split_on_monotonicity(values: Sequence[float], jump_tol: float,
                          min_group: int = 3) -> List[int]:
    """Indices where a sorted (descending) sequence jumps back up: panel starts.

    A waterfall panel is sorted, so values only fall left to right. A large rise
    cannot happen inside one panel; it means the next panel has begun. This finds
    panel boundaries that whitespace detection misses, which is the case when two
    panels abut with one panel's deepest bar against the next panel's tallest.

    ``min_group`` guards against a single mis-segmented bar manufacturing a panel:
    a split is only accepted when both sides are substantial.
    """
    candidates = [i for i in range(1, len(values))
                  if values[i] - values[i - 1] > jump_tol]
    starts: List[int] = []
    prev = 0
    for i in candidates:
        if i - prev >= min_group and len(values) - i >= min_group:
            starts.append(i)
            prev = i
    return starts


def touches_baseline(image: Image, colors: Sequence[RGB], baseline_py: int,
                     x_start: int, x_end: int, tol: int = 34,
                     band: int = 3) -> bool:
    """Does this column span carry a mark at the zero line?

    Real bars grow from the baseline; a legend swatch floats away from it. This
    is the invariant that keeps legends out of the measurement without needing to
    locate the legend box.
    """
    for x in range(x_start, x_end + 1):
        for y in range(baseline_py - band, baseline_py + band + 1):
            px = image.pixel(x, y)
            if any(Image.matches(px, c, tol) for c in colors):
                return True
    return False


def is_descending(values: Sequence[float], slack: float = 1.0) -> bool:
    """True if the sequence never rises by more than ``slack`` (sortedness)."""
    return all(values[i] - values[i - 1] <= slack for i in range(1, len(values)))
