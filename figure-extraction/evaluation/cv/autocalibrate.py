"""Automatic calibration: build a CV config for an unseen figure.

The hand-written per-figure config (axis anchors, bar colors, plot box) is what
keeps CV from running out of the gate on a new image. This module fills that
config automatically, with the same division of labor the whole system rests on:

  * deterministic CV finds the *geometry* it is good at (horizontal gridlines,
    dominant saturated colors, the plot's bounding box);
  * the VLM reads the *printed labels* it is good at (the axis tick values, the
    legend text), which is exactly the task it does not fail at.

Neither one eyeballs a bar height. The axis mapping is then built from two
anchors and *self-checked* against a third labelled gridline, so a bad
calibration is caught instead of trusted. When the self-check fails, the caller
should decline CV and fall back to the VLM read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from evaluation.cv.calibrate import LinearAxis, LogAxis
from evaluation.cv.image import RGB, Image


# --- geometric detection (deterministic) -----------------------------------

def detect_h_gridlines_scored(image: Image, x_range: Tuple[int, int],
                              min_frac: float = 0.4, merge_px: int = 3
                              ) -> List[Tuple[int, float]]:
    """Horizontal lines as (row_centre, peak_dark_fraction), top to bottom.

    A candidate row has at least ``min_frac`` of its pixels (over ``x_range``)
    dark. Adjacent rows merge into their centre; the run keeps its strongest
    fraction, so the solid baseline (near 1.0) is separable from dashed lines.
    """
    x0, x1 = x_range
    width = max(1, x1 - x0)
    hits: List[Tuple[int, float]] = []
    for y in range(image.height):
        dark = sum(1 for x in range(x0, x1) if max(image.pixel(x, y)[:3]) < 110)
        frac = dark / width
        if frac >= min_frac:
            hits.append((y, frac))
    merged: List[Tuple[int, float]] = []
    run: List[Tuple[int, float]] = []
    for y, f in hits:
        if run and y - run[-1][0] > merge_px:
            ys = [r[0] for r in run]
            merged.append((sum(ys) // len(ys), max(r[1] for r in run)))
            run = []
        run.append((y, f))
    if run:
        ys = [r[0] for r in run]
        merged.append((sum(ys) // len(ys), max(r[1] for r in run)))
    return merged


def detect_h_gridlines(image: Image, x_range: Tuple[int, int],
                       min_frac: float = 0.4, merge_px: int = 3) -> List[int]:
    """Row centres of the horizontal lines, top to bottom (see the scored form)."""
    return [r for r, _ in detect_h_gridlines_scored(image, x_range, min_frac, merge_px)]


def evenly_spaced_subset(rows: List[int], tol_frac: float = 0.25) -> List[int]:
    """Largest subset of rows with a near-constant gap (the major gridlines).

    Greedy: for each candidate starting gap, walk rows accepting the next one
    whose spacing is within ``tol_frac`` of the running gap. Returns the longest
    such chain, which filters dashed threshold lines and the odd stray edge.
    """
    if len(rows) < 2:
        return rows
    best: List[int] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            gap = rows[j] - rows[i]
            if gap <= 0:
                continue
            chain = [rows[i], rows[j]]
            for k in range(j + 1, len(rows)):
                expected = chain[-1] + gap
                if abs(rows[k] - expected) <= max(2, gap * tol_frac):
                    chain.append(rows[k])
            if len(chain) > len(best):
                best = chain
    return best or rows


def detect_dominant_colors(image: Image, sample_step: int = 2,
                           top: int = 4) -> List[RGB]:
    """Most common saturated (non-gray, non-white) colors, coarsely binned."""
    from collections import Counter
    counts: Counter = Counter()
    for y in range(0, image.height, sample_step):
        for x in range(0, image.width, sample_step):
            r, g, b = image.pixel(x, y)[:3]
            if min(r, g, b) > 235:
                continue
            if max(r, g, b) - min(r, g, b) < 25:
                continue
            counts[(r // 12 * 12, g // 12 * 12, b // 12 * 12)] += 1
    return [c for c, _ in counts.most_common(top)]


def detect_panels(image: Image, box: Tuple[int, int, int, int],
                  colors: List[RGB], tol: int = 34,
                  min_gap_frac: float = 0.03,
                  min_panel_frac: float = 0.12) -> List[Tuple[int, int]]:
    """Split a figure into panels by the vertical whitespace between charts.

    Column projection: for each x, does any bar colour appear? Panels are the
    runs of columns that contain marks, separated by gaps wider than
    ``min_gap_frac`` of the figure. This is deterministic, unlike asking a model
    how many panels there are, and it is the same signal that separates
    individual bars, just read at a coarser scale.

    Returns (x0, x1) pairs; a single-panel figure yields one pair.
    """
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    has_mark = []
    for x in range(x0, x1):
        found = False
        for y in range(y0, y1):
            px = image.pixel(x, y)
            if any(Image.matches(px, c, tol) for c in colors):
                found = True
                break
        has_mark.append(found)

    min_gap = max(3, int(min_gap_frac * width))
    panels: List[Tuple[int, int]] = []
    start: Optional[int] = None
    gap = 0
    for i, mark in enumerate(has_mark):
        if mark:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                panels.append((x0 + start, x0 + i - gap))
                start = None
                gap = 0
    if start is not None:
        panels.append((x0 + start, x0 + len(has_mark) - 1))

    min_w = min_panel_frac * width
    panels = [p for p in panels if (p[1] - p[0]) >= min_w]
    return panels or [(x0, x1)]


def detect_plot_box(image: Image) -> Tuple[int, int, int, int]:
    """Crude plot bounding box: the span of saturated color, padded inward.

    Good enough to fence out wide title/footer margins; the axis anchors carry
    the precise vertical mapping regardless.
    """
    xs, ys = [], []
    for y in range(0, image.height, 2):
        for x in range(0, image.width, 2):
            r, g, b = image.pixel(x, y)[:3]
            if min(r, g, b) <= 235 and max(r, g, b) - min(r, g, b) >= 25:
                xs.append(x); ys.append(y)
    if not xs:
        return (0, 0, image.width, image.height)
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


# --- VLM reads the printed labels ------------------------------------------

_AXIS_PROMPT = (
    "This is a scientific figure. Look ONLY at the {which} axis.\n"
    "List the numeric tick labels on that axis, in order from {order}.\n"
    "Respond with strict JSON: {{\"labels\": [<numbers>], \"scale\": \"linear\"|\"log\"}}.\n"
    "Use the sign shown (negative values are negative). No prose."
)

_REFLINE_PROMPT = (
    "This is a waterfall chart. It has horizontal reference lines: a solid line at "
    "zero and usually one or more dashed threshold lines (for example a RECIST -30% "
    "line, a +20% progression line, or PSA -50/-90 lines).\n"
    "List the value of every horizontal reference line you can see, in order from "
    "TOP to BOTTOM, using the sign shown.\n"
    "Respond with strict JSON: {\"values\": [<numbers top-to-bottom>]}. No prose."
)

_LEGEND_PROMPT = (
    "This figure has colored data series. For each series in the legend, give its "
    "color as a common name and its label text.\n"
    "Respond with strict JSON: {\"series\": [{\"color\": \"blue\", \"label\": \"...\"}]}. "
    "No prose."
)


def _extract_json(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def read_axis_labels(image_bytes: bytes, client, which: str = "y",
                     media_type: str = "image/png") -> Optional[dict]:
    order = "top to bottom" if which == "y" else "left to right"
    prompt = _AXIS_PROMPT.format(which=which, order=order)
    reply = client.complete(prompt, image=image_bytes, media_type=media_type, max_tokens=400)
    return _extract_json(reply)


_LAYOUT_PROMPT = (
    "This image contains one or more WATERFALL bar charts (bars rising/falling from "
    "a zero baseline). Some figures place two waterfall panels side by side, or pair "
    "a waterfall with an unrelated plot (e.g. a swimmer plot).\n"
    "A PANEL is an entire sub-chart with its own plot area, NOT an individual bar. "
    "A figure almost always has between 1 and 3 panels. If the figure is a single "
    "chart, report exactly ONE panel spanning it.\n"
    "Report the layout so a measurement tool can process each waterfall panel "
    "separately. Give each WATERFALL panel's horizontal extent as a fraction of the "
    "image width (0=left edge, 1=right edge). Ignore non-waterfall panels.\n"
    "Also give the numeric values of the horizontal reference lines (zero baseline and "
    "dashed thresholds), top to bottom, and the approximate number of bars per panel "
    "if visible."
)


#: Schema for the layout read. Bound as a tool's ``input_schema`` so the reply is
#: guaranteed to conform; no prompt wording can make the model emit bad JSON.
LAYOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "panels": {
            "type": "array",
            "description": "One entry per WATERFALL panel, left to right.",
            "items": {
                "type": "object",
                "properties": {
                    "x0": {"type": "number",
                           "description": "Left edge as a fraction of image width (0-1)."},
                    "x1": {"type": "number",
                           "description": "Right edge as a fraction of image width (0-1)."},
                    "n_bars": {"type": ["integer", "null"],
                               "description": "Approximate bar count, or null."},
                },
                "required": ["x0", "x1"],
            },
        },
        "reference_values": {
            "type": "array",
            "description": "Horizontal reference-line values, top to bottom.",
            "items": {"type": "number"},
        },
    },
    "required": ["panels", "reference_values"],
}


def read_layout(image_bytes: bytes, client,
                media_type: str = "image/png") -> Optional[dict]:
    """VLM detection of the waterfall panels, reference lines, and bar counts.

    This is the generalist detection front-end: it needs no per-figure config and
    works on an unseen image. Panels are returned as width-fractions so a
    multi-panel figure can be measured one panel at a time.

    Uses the schema-bound structured call when the client supports it, so the
    result cannot come back as malformed JSON; falls back to free-text parsing
    only for a client that has no ``complete_json``.
    """
    if hasattr(client, "complete_json"):
        js = client.complete_json(_LAYOUT_PROMPT, LAYOUT_SCHEMA,
                                  image=image_bytes, media_type=media_type,
                                  max_tokens=800, tool_name="report_layout")
    else:
        js = _extract_json(client.complete(_LAYOUT_PROMPT, image=image_bytes,
                                           media_type=media_type, max_tokens=500))
    if not js or "panels" not in js:
        return None
    return _sane_layout(js)


#: A schema guarantees the *shape* of the reply, not that it makes sense. On a
#: dense chart the model sometimes labels each *bar* a panel. Width separates the
#: two cleanly: a real panel spans a large fraction of the figure, a single bar
#: spans ~1%. Filtering on width keeps genuine multi-panel splits intact, which a
#: blunt "too many panels, use the whole image" rule would throw away.
MIN_PANEL_WIDTH_FRAC = 0.15


def _sane_layout(js: dict) -> dict:
    """Drop malformed and implausibly narrow panels; fall back to one full-width
    panel only if nothing survives."""
    panels = []
    for p in js.get("panels") or []:
        if not isinstance(p, dict):
            continue
        try:
            x0, x1 = float(p.get("x0")), float(p.get("x1"))
        except (TypeError, ValueError):
            continue
        x0, x1 = max(0.0, x0), min(1.0, x1)
        if x1 - x0 >= MIN_PANEL_WIDTH_FRAC:
            panels.append({"x0": x0, "x1": x1, "n_bars": p.get("n_bars")})
    if not panels:
        panels = [{"x0": 0.0, "x1": 1.0, "n_bars": None}]
    js["panels"] = sorted(panels, key=lambda p: p["x0"])
    return js


def read_reference_lines(image_bytes: bytes, client,
                         media_type: str = "image/png") -> Optional[List[float]]:
    """The values of the drawn horizontal reference lines, top to bottom.

    Waterfalls reliably print a zero baseline and labelled dashed thresholds, and
    those lines are exactly what CV detects, so this is the calibration signal
    that actually aligns (unlike unmarked numeric ticks)."""
    reply = client.complete(_REFLINE_PROMPT, image=image_bytes,
                            media_type=media_type, max_tokens=200)
    js = _extract_json(reply)
    if not js or "values" not in js:
        return None
    try:
        return [float(v) for v in js["values"]]
    except (TypeError, ValueError):
        return None


# --- assembling a self-checked axis ----------------------------------------

@dataclass
class AutoAxis:
    axis: object                       # LinearAxis or LogAxis
    anchors: Tuple[Tuple[int, float], Tuple[int, float]]
    check_error: Optional[float]       # worst |predicted - labeled| on held-out ticks
    check_unit: str                    # the axis unit the error is in
    ok: bool
    note: str = ""


def build_axis(image: Image, image_bytes: bytes, client,
               x_range: Tuple[int, int], which: str = "y",
               check_tol: Optional[float] = None,
               values: Optional[List[float]] = None) -> Optional[AutoAxis]:
    """Detect the drawn horizontal lines, have the VLM name their values, map,
    and self-check.

    Anchoring is baseline-first, because a waterfall's zero line is the single
    most reliable thing to detect (the strongest full-width line) and its value is
    known to be 0. The VLM supplies the threshold values; the largest-magnitude one
    anchors the scale against the detected line furthest from the baseline. Any
    remaining threshold is a held-out self-check.
    """
    if values is None:
        values = read_reference_lines(image_bytes, client, media_type=_media(image))
    # Low threshold so dashed/dotted threshold lines are caught; the baseline is
    # still separable as the strongest line.
    scored = detect_h_gridlines_scored(image, x_range, min_frac=0.2)
    if not values or len(scored) < 2:
        return None

    baseline_row, _ = max(scored, key=lambda rf: rf[1])   # strongest line -> 0
    other_rows = [r for r, _ in scored if r != baseline_row]
    thresholds = [v for v in values if abs(v) > 1e-6]      # drop a reported 0
    if not other_rows or not thresholds:
        return None

    v_ext = max(thresholds, key=abs)                       # e.g. -30 or -90
    # The anchor line must be on the correct side of the baseline: a negative
    # threshold sits BELOW zero (larger row), a positive one ABOVE (smaller row).
    side = [r for r in other_rows if (r > baseline_row) == (v_ext < 0)]
    if not side:
        return None
    r_ext = max(side, key=lambda r: abs(r - baseline_row))
    if r_ext == baseline_row:
        return None
    axis = LinearAxis(baseline_row, 0.0, r_ext, v_ext)

    # Self-check: every other reported threshold should land on a detected line.
    worst = None
    for v in thresholds:
        if v == v_ext:
            continue
        pred_row = axis.to_pixel(v)
        nearest = min(other_rows, key=lambda r: abs(r - pred_row))
        err = abs(axis.to_value(nearest) - v)
        worst = err if worst is None else max(worst, err)
    span = abs(v_ext) or 1.0
    tol = check_tol if check_tol is not None else 0.06 * span
    ok = (worst is None) or (worst <= tol)
    return AutoAxis(axis=axis, anchors=((baseline_row, 0.0), (r_ext, v_ext)),
                    check_error=worst, check_unit="value", ok=ok,
                    note=f"baseline@{baseline_row}, {len(thresholds)} thresholds, tol={tol:.2f}")


def _media(image: Image) -> str:
    return "image/png"


# --- full waterfall config from scratch ------------------------------------

def auto_waterfall_config(image: Image, image_bytes: bytes, client,
                          quantities: dict) -> Optional[dict]:
    """Build a one-panel waterfall config with no hand tuning.

    Returns None (decline CV) if the axis self-check fails.
    """
    box = detect_plot_box(image)
    x_range = (box[0] + 2, box[2] - 2)
    auto = build_axis(image, image_bytes, client, x_range=x_range, which="y")
    if auto is None or not auto.ok:
        return None
    axis = auto.axis
    baseline_py = int(round(axis.to_pixel(0.0)))
    colors = detect_dominant_colors(image, top=1)
    if not colors:
        return None
    (r0, v0), (r1, v1) = auto.anchors
    return {
        "figure_type": "waterfall",
        "_auto": {"check_error": auto.check_error, "note": auto.note,
                  "anchors": [[r0, v0], [r1, v1]]},
        "panels": [{
            "name": "auto",
            "bar_color": list(colors[0]),
            "tol": 40,
            "min_run_px": 3,
            "max_gap_px": 1,
            "baseline_py": baseline_py,
            "x_range": [x_range[0], x_range[1]],
            "y_range": [box[1] + 2, box[3] - 2],
            "y_axis": {"type": "linear", "px1": r0, "val1": v0, "px2": r1, "val2": v1},
            "quantities": quantities,
        }],
    }
