"""Learned perception in the detection slot.

The hand-coded detectors this replaces needed a new rule for every figure: a
contiguity walk to dodge legend swatches, gap-bridging to survive a dashed line
crossing a bar, a monotonicity split for abutting panels, a whitespace split for
a neighbouring plot, and a dark-pixel scan that simply cannot see a light grey
reference line. Eleven rules for four figures, with no sign of converging.

A model that has already learned what a legend, an axis, a dashed threshold and a
panel *look like* does not need any of them. So detection moves to the VLM, and
the labour splits by what each side is actually good at:

* the **VLM** says what exists and roughly where (panels, legend boxes, reference
  lines and their values, series colours, stated N) -- recognition, at coarse
  spatial precision;
* **deterministic CV** refines each reported location to the exact pixel and does
  all measurement -- precision, over a tiny search window rather than the whole
  image;
* **invariants** verify the result and decline it when they fail.

Refinement is what makes the coarse spatial precision of a model acceptable: we
never trust a reported coordinate, we use it only to know *where to look*. Finding
a faint line within fifteen rows of a stated position is easy where finding it
anywhere on the page is not.
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from evaluation.cv.image import RGB, Image

# --- what we ask the model to detect ---------------------------------------

DETECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "plot_region": {
            "type": "object",
            "description": "Bounding box of the data area only, excluding titles, "
                           "axis labels and legends. Fractions of image size.",
            "properties": {
                "x0": {"type": "number"}, "y0": {"type": "number"},
                "x1": {"type": "number"}, "y1": {"type": "number"},
            },
            "required": ["x0", "y0", "x1", "y1"],
        },
        "panels": {
            "type": "array",
            "description": "One entry per WATERFALL chart (a whole sub-chart with "
                           "its own plot area, never an individual bar). Left to right.",
            "items": {
                "type": "object",
                "properties": {
                    "x0": {"type": "number", "description": "Left edge, fraction of width."},
                    "x1": {"type": "number", "description": "Right edge, fraction of width."},
                    "title": {"type": ["string", "null"]},
                    "n_bars": {"type": ["integer", "null"],
                               "description": "Stated N for this panel if printed, else null."},
                },
                "required": ["x0", "x1"],
            },
        },
        "legend_boxes": {
            "type": "array",
            "description": "Bounding boxes of legends/keys, so they can be excluded "
                           "from measurement. Empty if none.",
            "items": {
                "type": "object",
                "properties": {
                    "x0": {"type": "number"}, "y0": {"type": "number"},
                    "x1": {"type": "number"}, "y1": {"type": "number"},
                },
                "required": ["x0", "y0", "x1", "y1"],
            },
        },
        "reference_lines": {
            "type": "array",
            "description": "Every horizontal reference line: the zero baseline and "
                           "any dashed/solid threshold lines, including faint ones.",
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "The value it marks."},
                    "y": {"type": "number",
                          "description": "Vertical position as a fraction of image height."},
                },
                "required": ["value", "y"],
            },
        },
    },
    "required": ["plot_region", "panels", "reference_lines"],
}

DETECTION_PROMPT = (
    "You are looking at a scientific figure containing one or more WATERFALL bar "
    "charts (bars rising and falling from a zero baseline).\n"
    "Identify its layout precisely so a measurement tool knows where to look.\n"
    "- plot_region: the data area only, excluding titles, axis labels and legends.\n"
    "- panels: one per waterfall chart. A panel is a whole sub-chart, NEVER a single "
    "bar. If two waterfalls sit side by side, report two panels. Ignore any "
    "non-waterfall plot (e.g. a swimmer plot) entirely.\n"
    "- legend_boxes: any legend or colour key, so it can be excluded.\n"
    "- reference_lines: EVERY horizontal reference line with the value it marks, "
    "including faint or light-grey ones. Give each one's vertical position as a "
    "fraction of image height.\n"
    "  IMPORTANT - signs: report the value as plotted on the axis, not as written "
    "in the label. A line labelled '30% decline' or '30% reduction' sits BELOW "
    "zero and must be reported as -30. A line labelled '+20%' or 'progression' "
    "sits above zero and is +20. A line below the zero baseline is always "
    "negative.\n"
    "All coordinates are fractions (0=left/top, 1=right/bottom). Approximate "
    "positions are fine; they will be refined against the pixels."
)


#: A second, independent read of the same quantities the CV measurement produces.
#: The point is not accuracy -- the model is worse at this than pixel measurement
#: -- it is *independence*. A single reader cannot detect its own systematic bias;
#: our CV under-counts by a consistent ~4% and nothing in the pipeline notices,
#: because there is nothing to disagree with it. A reader whose failure modes are
#: unrelated makes that bias visible as disagreement.
VALUES_SCHEMA = {
    "type": "object",
    "properties": {
        "panels": {
            "type": "array",
            "description": "One entry per waterfall panel, left to right.",
            "items": {
                "type": "object",
                "properties": {
                    "n_bars": {"type": ["integer", "null"],
                               "description": "How many bars this panel plots."},
                    "leftmost_value": {"type": ["number", "null"],
                                       "description": "Value of the leftmost bar."},
                    "deepest_value": {"type": ["number", "null"],
                                      "description": "Most negative bar value."},
                    "count_beyond_plus20": {"type": ["integer", "null"],
                                            "description": "Bars at or above +20%."},
                    "count_beyond_minus30": {"type": ["integer", "null"],
                                             "description": "Bars at or below -30%."},
                    "count_beyond_minus50": {"type": ["integer", "null"],
                                             "description": "Bars at or below -50%."},
                },
            },
        },
    },
    "required": ["panels"],
}

VALUES_PROMPT = (
    "Read this waterfall chart directly and report the values you see. Work panel "
    "by panel, left to right; if there is only one waterfall panel, report one "
    "entry. Ignore any non-waterfall plot in the image.\n"
    "For each panel give: how many bars it plots, the leftmost bar's value, the "
    "most negative bar's value, and how many bars fall at or beyond +20%, -30% and "
    "-50%.\n"
    "Read the values off the axis as plotted (a bar below zero is negative). Use "
    "null for anything you genuinely cannot determine rather than guessing."
)


def read_values(image_bytes: bytes, client,
                media_type: str = "image/png") -> List[dict]:
    """Ask the model to read the chart's quantities, independently of CV.

    Returns the per-panel dicts, left to right (empty if unavailable). A bound
    schema fixes the shape of the reply but not the model's occasional habit of
    nesting the whole answer as a JSON *string* in the first field, so the result
    is normalised here rather than trusted.
    """
    if not hasattr(client, "complete_json"):
        return []
    js = client.complete_json(VALUES_PROMPT, VALUES_SCHEMA, image=image_bytes,
                              media_type=media_type, max_tokens=1200,
                              tool_name="report_values")
    return _normalise_panels(js)


def _normalise_panels(js) -> List[dict]:
    """Pull a list of per-panel dicts out of whatever shape came back."""
    for _ in range(3):                       # unwrap at most a few nestings
        if js is None:
            return []
        if isinstance(js, str):
            try:
                js = json.loads(js)
            except json.JSONDecodeError:
                return []
            continue
        if isinstance(js, dict):
            js = js.get("panels")
            continue
        break
    if not isinstance(js, list):
        return []
    return [p for p in js if isinstance(p, dict)]


@dataclass
class RefLine:
    value: float
    y_frac: float
    row: Optional[int] = None          # filled by refinement
    strength: float = 0.0


@dataclass
class Detection:
    plot_region: Tuple[int, int, int, int]
    panels: List[Tuple[int, int]] = field(default_factory=list)
    panel_n: List[Optional[int]] = field(default_factory=list)
    legend_boxes: List[Tuple[int, int, int, int]] = field(default_factory=list)
    ref_lines: List[RefLine] = field(default_factory=list)


def detect(image: Image, image_bytes: bytes, client,
           media_type: str = "image/png") -> Optional[Detection]:
    """Ask the model what is in the figure, in pixel coordinates."""
    if not hasattr(client, "complete_json"):
        return None
    js = client.complete_json(DETECTION_PROMPT, DETECTION_SCHEMA,
                              image=image_bytes, media_type=media_type,
                              max_tokens=1500, tool_name="report_detection")
    if not js:
        return None
    W, H = image.width, image.height

    def box(d: dict) -> Tuple[int, int, int, int]:
        return (int(d["x0"] * W), int(d["y0"] * H),
                int(d["x1"] * W), int(d["y1"] * H))

    pr = js.get("plot_region") or {"x0": 0, "y0": 0, "x1": 1, "y1": 1}
    det = Detection(plot_region=box(pr))

    for p in js.get("panels") or []:
        if not isinstance(p, dict):
            continue
        try:
            x0, x1 = int(float(p["x0"]) * W), int(float(p["x1"]) * W)
        except (KeyError, TypeError, ValueError):
            continue
        if x1 - x0 > 0.05 * W:          # ignore a "panel" the size of a bar
            det.panels.append((max(0, x0), min(W, x1)))
            det.panel_n.append(p.get("n_bars"))
    if not det.panels:
        det.panels = [(det.plot_region[0], det.plot_region[2])]
        det.panel_n = [None]

    for lb in js.get("legend_boxes") or []:
        if isinstance(lb, dict):
            try:
                det.legend_boxes.append(box(lb))
            except (KeyError, TypeError, ValueError):
                continue

    for rl in js.get("reference_lines") or []:
        if not isinstance(rl, dict):
            continue
        try:
            det.ref_lines.append(RefLine(value=float(rl["value"]),
                                         y_frac=float(rl["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    return det


# --- deterministic refinement ----------------------------------------------

def line_score(image: Image, y: int, x_range: Tuple[int, int],
               colors: Sequence[RGB], tol: int = 34) -> float:
    """How much does row ``y`` look like a horizontal rule?

    Counts pixels that are neither background nor a series colour: reference
    lines are drawn in greys and blacks that the bars are not. Deliberately not a
    darkness threshold, because the lines we most need to find (a light grey
    "30% decline" rule) are not dark at all.
    """
    x0, x1 = x_range
    width = max(1, x1 - x0)
    hits = 0
    for x in range(x0, x1):
        r, g, b = image.pixel(x, y)[:3]
        if min(r, g, b) > 240:                       # background
            continue
        px = (r, g, b)
        if any(Image.matches(px, c, tol) for c in colors):   # a bar, not a rule
            continue
        hits += 1
    return hits / width


def refine_reference_line(image: Image, y_approx: int, x_range: Tuple[int, int],
                          colors: Sequence[RGB], window: int = 18,
                          min_score: float = 0.30) -> Tuple[Optional[int], float]:
    """Snap a reported line position to the strongest rule row nearby.

    Searching a small window around a reported position is a far easier problem
    than locating the line on the whole page, which is why a coarse detection is
    enough to calibrate from.
    """
    best_row, best = None, 0.0
    lo = max(0, y_approx - window)
    hi = min(image.height, y_approx + window + 1)
    for y in range(lo, hi):
        s = line_score(image, y, x_range, colors)
        if s > best:
            best_row, best = y, s
    if best_row is None or best < min_score:
        return None, best
    return best_row, best


def refine_all(image: Image, det: Detection, colors: Sequence[RGB],
               window: int = 18, min_score: float = 0.30) -> List[RefLine]:
    """Refine every detected reference line; drop the ones with no pixel support."""
    x0, _, x1, _ = det.plot_region
    kept: List[RefLine] = []
    for rl in det.ref_lines:
        y_approx = int(rl.y_frac * image.height)
        row, score = refine_reference_line(image, y_approx, (x0, x1), colors,
                                           window=window, min_score=min_score)
        if row is not None:
            rl.row, rl.strength = row, score
            kept.append(rl)
    # Two lines mapping to the same row cannot both be right; keep the stronger.
    by_row: Dict[int, RefLine] = {}
    for rl in sorted(kept, key=lambda r: -r.strength):
        by_row.setdefault(rl.row, rl)
    det.ref_lines = sorted(by_row.values(), key=lambda r: r.row)
    return det.ref_lines


def find_line_rows(image: Image, x_range: Tuple[int, int], colors: Sequence[RGB],
                   min_score: float = 0.25, merge_px: int = 4
                   ) -> List[Tuple[int, float]]:
    """Every row that looks like a horizontal rule, as (row, score), top to bottom.

    Uses :func:`line_score`, so it sees light grey rules as readily as black ones.
    Positions come from here rather than from the model: a reported y is easily
    tens of rows out, while a rule's actual row is unambiguous in the pixels.
    """
    raw = [(y, line_score(image, y, x_range, colors)) for y in range(image.height)]
    raw = [(y, s) for y, s in raw if s >= min_score]
    merged: List[Tuple[int, float]] = []
    run: List[Tuple[int, float]] = []
    for y, s in raw:
        if run and y - run[-1][0] > merge_px:
            best = max(run, key=lambda t: t[1])
            merged.append((sum(t[0] for t in run) // len(run), best[1]))
            run = []
        run.append((y, s))
    if run:
        best = max(run, key=lambda t: t[1])
        merged.append((sum(t[0] for t in run) // len(run), best[1]))
    return merged


def _fit(pairs: Sequence[Tuple[int, float]]):
    """Least-squares (row -> value) fit; returns (slope, intercept, max_residual)."""
    n = len(pairs)
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] * p[0] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None, None, None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    residual = max(abs(v - (slope * r + intercept)) for r, v in pairs)
    return slope, intercept, residual


def calibrate_from_lines(image: Image, x_range: Tuple[int, int],
                         colors: Sequence[RGB], values: Sequence[float],
                         min_score: float = 0.25, accept_tol: float = 1.0,
                         max_rows: int = 12):
    """Pair detected rule rows with model-reported values by best collinearity.

    The model is reliable about *which* values are marked and their top-to-bottom
    order, and unreliable about where they sit; the pixels are the reverse. So the
    rows come from :func:`find_line_rows`, the values from the model, and the
    pairing is chosen as the order-preserving assignment whose (row, value) points
    are most collinear.

    Collinearity is the right criterion because a linear axis *is* collinearity,
    which makes the winning fit's residual a genuine self-check rather than a
    restatement of the assumption: a wrong pairing does not lie on a line.

    Not every reported line is real, so the search may use a *subset*: it prefers
    the largest set that fits within ``accept_tol`` and otherwise returns the best
    fit it found. Forcing every candidate into the fit lets one spurious rule
    (an axis-label row read as the zero line) reject a figure whose other rules
    are perfectly collinear.

    Returns ``(LinearAxis, residual, pairs)`` or ``(None, None, [])``.
    """
    from itertools import combinations

    from evaluation.cv.calibrate import LinearAxis

    rows = find_line_rows(image, x_range, colors, min_score=min_score)
    vals = sorted({float(v) for v in values}, reverse=True)   # top to bottom
    if len(rows) < 2 or len(vals) < 2:
        return None, None, []

    # Keep the strongest candidates, in row order, to bound the search.
    rows = sorted(sorted(rows, key=lambda t: -t[1])[:max_rows], key=lambda t: t[0])

    def best_for(k: int):
        found = None
        for row_sel in combinations(rows, k):
            rs = [r for r, _ in row_sel]
            for val_sel in combinations(vals, k):
                pairs = list(zip(rs, val_sel))
                slope, intercept, residual = _fit(pairs)
                if slope is None or slope == 0 or residual is None:
                    continue
                # Strength breaks ties toward the rules with most pixel support.
                strength = sum(s for _, s in row_sel)
                score = (residual, -strength)
                if found is None or score < found[0]:
                    found = (score, slope, intercept, residual, pairs)
        return found

    # Drop outliers rather than forcing every reported line into the fit. One
    # spurious rule (an axis-label row mistaken for the zero line) otherwise
    # poisons an otherwise perfectly collinear set, and the whole figure is
    # declined for a single bad point. Prefer the largest set that is genuinely
    # collinear; fall back to the best fit of any size.
    kmax = min(len(rows), len(vals))
    best = None
    for k in range(kmax, 1, -1):
        cand = best_for(k)
        if cand is None:
            continue
        if best is None or cand[3] < best[3]:
            best = cand
        if k >= 3 and cand[3] <= accept_tol:
            best = cand
            break
    if best is None:
        return None, None, []
    _, slope, intercept, residual, pairs = best
    r0 = pairs[0][0]
    r1 = pairs[-1][0] if pairs[-1][0] != r0 else r0 + 1
    axis = LinearAxis(r0, slope * r0 + intercept, r1, slope * r1 + intercept)
    return axis, residual, pairs


def fit_axis(ref_lines: Sequence[RefLine]):
    """Least-squares pixel->value axis over every refined reference line.

    Fitting all the lines rather than anchoring on two uses the redundancy the
    figure already provides, and the worst residual is a genuine self-check: if
    the lines are not collinear in (row, value), either a line was misidentified
    or the axis is not linear, and the read should be declined.

    Returns ``(LinearAxis, max_residual)``, or ``(None, None)`` if under-determined.
    """
    from evaluation.cv.calibrate import LinearAxis

    pts = [(rl.row, rl.value) for rl in ref_lines if rl.row is not None]
    if len(pts) < 2:
        return None, None
    n = len(pts)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None, None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    if slope == 0:
        return None, None
    residual = max(abs(y - (slope * x + intercept)) for x, y in pts)
    r0 = pts[0][0]
    r1 = pts[-1][0] if pts[-1][0] != r0 else r0 + 1
    axis = LinearAxis(r0, slope * r0 + intercept, r1, slope * r1 + intercept)
    return axis, residual


def in_legend(det: Detection, x: int, y: int) -> bool:
    """Is this pixel inside a detected legend box (and so not data)?"""
    for lx0, ly0, lx1, ly1 in det.legend_boxes:
        if lx0 <= x <= lx1 and ly0 <= y <= ly1:
            return True
    return False
