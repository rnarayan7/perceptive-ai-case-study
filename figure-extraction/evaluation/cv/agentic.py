"""Calibration as a loop rather than a single blind shot.

Every one-shot attempt at calibrating the Kaplan-Meier, PK and forest figures
failed the same way: the model read the axis labels perfectly every time, and
deterministic geometry could not tell a tick from a caption, a curve from a
title, or an axis rule from a summary banner. Telling data apart from decoration
is recognition work, and it was being asked of the half that cannot do it.

Calibrating those figures by hand worked, and not because of better eyesight. It
worked because there was a loop: propose something, measure with it, notice the
result was absurd, and revise. This gives the automated path the same loop.

The division of labour is inverted from the one-shot version. Deterministic CV
does not decide anything; it enumerates *candidates* (every horizontal rule,
every vertical rule, where each series colour begins and ends). The model then
chooses which candidate carries which value, which is recognition, and the thing
it has been reliable at throughout. The proposal is measured, checked against
properties that must hold of any correct reading, and when a check fails the
violation is handed back so the next attempt can avoid it.

The checks need no answer key. A survival curve starts at 1.0 and never rises. A
confidence interval brackets its point estimate. A concentration is positive.
Those are true of a correct reading of any such figure, so the loop works on a
figure it has never seen.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from evaluation.cv.image import RGB, Image

# --- what CV offers the model to choose from -------------------------------


def horizontal_rules(image: Image, min_frac: float = 0.35,
                     merge: int = 3) -> List[Tuple[int, float]]:
    """Every row that reads as a rule, as (row, coverage). No interpretation."""
    w = image.width
    hits = []
    for y in range(image.height):
        dark = sum(1 for x in range(w) if max(image.pixel(x, y)[:3]) < 150)
        if dark / w >= min_frac:
            hits.append((y, dark / w))
    return _merge_scored(hits, merge)


def vertical_rules(image: Image, y_range: Tuple[int, int],
                   min_frac: float = 0.20, merge: int = 3) -> List[Tuple[int, float]]:
    """Every column that reads as a rule, as (column, coverage)."""
    y0, y1 = y_range
    h = max(1, y1 - y0)
    hits = []
    for x in range(image.width):
        dark = sum(1 for y in range(y0, y1) if max(image.pixel(x, y)[:3]) < 150)
        if dark / h >= min_frac:
            hits.append((x, dark / h))
    return _merge_scored(hits, merge)


def _merge_scored(hits: Sequence[Tuple[int, float]], gap: int) -> List[Tuple[int, float]]:
    out: List[Tuple[int, float]] = []
    run: List[Tuple[int, float]] = []
    for p, s in hits:
        if run and p - run[-1][0] > gap:
            ps = [t[0] for t in run]
            out.append((sum(ps) // len(ps), max(t[1] for t in run)))
            run = []
        run.append((p, s))
    if run:
        ps = [t[0] for t in run]
        out.append((sum(ps) // len(ps), max(t[1] for t in run)))
    return out


def series_extents(image: Image, colors: Sequence[RGB], tol: int = 45,
                   trim: float = 0.02) -> Dict[str, Dict[str, int]]:
    """Where each series colour actually appears: its bounding extent.

    Offered as a candidate rather than used directly, because a curve colour can
    collide with page text: the grey of a placebo arm is also the grey of a
    title, which is how one attempt derived a survival probability of 1.0 from
    the headline.
    """
    out: Dict[str, Dict[str, int]] = {}
    for c in colors:
        xs, ys = [], []
        for y in range(0, image.height, 2):
            for x in range(0, image.width, 2):
                if Image.matches(image.pixel(x, y), c, tol):
                    xs.append(x); ys.append(y)
        if xs:
            # Two readings of the same colour. The full extent is what the model
            # is shown, because a stray match is itself worth seeing. The trimmed
            # extent is what the checks use: a legend swatch or a letter stroke
            # in the matching colour is a handful of pixels, and taking the
            # outermost one as the edge of the data put the Kaplan-Meier curves
            # at x 24 to 1228 on a plot that runs from 138 to 1150.
            xs.sort(); ys.sort()
            lo_i, hi_i = int(len(xs) * trim), min(len(xs) - 1, int(len(xs) * (1 - trim)))
            out[",".join(map(str, c))] = {
                "first_x": min(xs), "last_x": max(xs),
                "top_y": min(ys), "bottom_y": max(ys),
                "core_first_x": xs[lo_i], "core_last_x": xs[hi_i],
                "core_top_y": ys[lo_i], "core_bottom_y": ys[hi_i],
            }
    return out


# --- the model chooses among them ------------------------------------------

CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "y_high": {"type": "object", "description": "Axis row carrying the larger y value.",
                   "properties": {"row": {"type": "integer"}, "value": {"type": "number"}},
                   "required": ["row", "value"]},
        "y_low": {"type": "object", "description": "Axis row carrying the smaller y value.",
                  "properties": {"row": {"type": "integer"}, "value": {"type": "number"}},
                  "required": ["row", "value"]},
        "x_left": {"type": "object", "description": "Axis column at the left end of the x range.",
                   "properties": {"col": {"type": "integer"}, "value": {"type": "number"}},
                   "required": ["col", "value"]},
        "x_right": {"type": "object", "description": "Axis column at the right end of the x range.",
                    "properties": {"col": {"type": "integer"}, "value": {"type": "number"}},
                    "required": ["col", "value"]},
        "y_scale": {"type": "string", "description": "linear or log"},
        "series": {
            "type": "array",
            "description": "One entry per plotted series, using a colour from the candidate list.",
            "items": {"type": "object",
                      "properties": {"color": {"type": "string",
                                               "description": "Exactly as listed, e.g. '96,132,12'."},
                                     "label": {"type": "string"}},
                      "required": ["color", "label"]},
        },
        "reasoning": {"type": "string", "description": "Why these candidates, in one or two sentences."},
    },
    "required": ["y_high", "y_low", "x_left", "x_right", "y_scale", "series"],
}

_PROMPT = """You are calibrating a {figure_type} figure so a measurement tool can read values off it.

Deterministic image analysis has found these candidate positions. Some are axis
lines and ticks; others are captions, banners, titles or legend rules. Choose
which ones are the axis, and say what value each carries.

Candidate horizontal rules (row, how much of the width it spans):
{h_rules}

Candidate vertical rules (column, how much of the height it spans):
{v_rules}

Where each series colour appears:
{extents}

Read the axis labels off the image itself to decide the values. The lists above
are hints, not a menu: if you can see a tick or gridline the detector missed,
give its pixel position directly. Tick marks are short and are routinely missed,
while page titles and banner rules are routinely found, so the strongest
candidate in a list is often not part of the plot at all.
{extra}{feedback}"""


def choose(image_bytes: bytes, client, figure_type: str, h_rules, v_rules,
           extents: dict, extra: str = "", feedback: str = "",
           media_type: str = "image/png") -> Optional[dict]:
    if not hasattr(client, "complete_json"):
        return None
    prompt = _PROMPT.format(
        figure_type=figure_type,
        h_rules=", ".join(f"{r}({s:.2f})" for r, s in h_rules) or "none",
        v_rules=", ".join(f"{c}({s:.2f})" for c, s in v_rules) or "none",
        extents="\n".join(f"  {k}: x {v['first_x']}-{v['last_x']}, y {v['top_y']}-{v['bottom_y']}"
                          for k, v in extents.items()) or "  none",
        extra=("\n" + extra) if extra else "",
        feedback=("\n\nThe previous attempt was measured and rejected:\n" + feedback
                  + "\nChoose differently.") if feedback else "")
    return client.complete_json(prompt, CHOICE_SCHEMA, image=image_bytes,
                                media_type=media_type, max_tokens=1200,
                                tool_name="choose_calibration")


# --- invariants that need no answer key ------------------------------------

@dataclass
class Attempt:
    """One turn of the loop: what was chosen, what it measured, what broke."""

    round: int
    choice: dict
    values: Dict[str, str] = field(default_factory=dict)
    violations: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def curve_starts_at_one(image: Image, colors: Sequence[RGB], y_axis,
                        x_left: int, axis_row: int, top_row: int = 0,
                        tol: int = 45, slack: float = 0.06) -> List[str]:
    """The strongest check available: a survival curve begins at 1.0.

    Bounds on plausibility are far too weak on their own. A calibration that put
    probability 1.0 on the title row produced a six-month survival of 0.37 and a
    median of 3.9 months, every one of which sits comfortably inside the range a
    probability may take. Reading the curve's own starting height under the
    proposed calibration is what separates a wrong reading from a right one,
    because only one mapping puts it at 1.0.
    """
    bad = []
    for c in colors:
        start = None
        # Bounded to the plot area. Searching the whole page finds the title,
        # whose grey matches a placebo arm, and the check then rejects a correct
        # calibration. A wrong invariant is worse than a missing one, because its
        # verdict is fed back and steers the next attempt away from the answer.
        lo = max(0, top_row - 10)
        for x in range(x_left, min(image.width, x_left + 160)):
            ys = [y for y in range(lo, axis_row) if Image.matches(image.pixel(x, y), c, tol)]
            if ys:
                start = min(ys)
                break
        if start is None:
            continue
        v = y_axis.to_value(start)
        if abs(v - 1.0) > slack:
            bad.append(f"under this calibration the {','.join(map(str,c))} curve begins at "
                       f"{v:.2f} rather than 1.0, which a survival curve must do at time zero")
    return bad


def check_km(values: Dict[str, float], choice: dict) -> List[str]:
    """A survival curve starts at 1.0, never rises, and stays within [0, 1]."""
    bad = []
    for key, v in values.items():
        if key.startswith("pfs_") and v is not None and not (0.0 <= v <= 1.0):
            bad.append(f"{key} came out {v:.2f}, outside the 0 to 1 range a probability must lie in")
        if key.startswith("median_") and v is not None and v <= 0:
            bad.append(f"{key} came out {v:.2f}, but a median survival time must be positive")
    yh, yl = choice.get("y_high", {}), choice.get("y_low", {})
    if yh.get("row") is not None and yl.get("row") is not None and yh["row"] >= yl["row"]:
        bad.append(f"y_high row {yh['row']} is not above y_low row {yl['row']} on the page")
    return bad


def check_forest(rows: List[Tuple[str, float, float, float]]) -> List[str]:
    """A hazard ratio is positive and its interval brackets the point estimate."""
    bad = []
    for label, point, lo, hi in rows:
        if point is not None and point <= 0:
            bad.append(f"{label} has hazard ratio {point:.2f}; a ratio must be positive")
        if None not in (point, lo, hi) and not (lo <= point <= hi):
            bad.append(f"{label} has interval {lo:.2f} to {hi:.2f} which does not contain its "
                       f"point estimate {point:.2f}")
    return bad


def check_pk(values: Dict[str, float]) -> List[str]:
    """Concentrations are positive, and a decay curve falls rather than rises."""
    bad = []
    for key, v in values.items():
        if v is not None and v <= 0:
            bad.append(f"{key} came out {v}, but a concentration must be positive")
    return bad


def run_loop(propose: Callable[[str], Optional[dict]],
             measure: Callable[[dict], Tuple[Dict[str, str], List[str]]],
             max_rounds: int = 3) -> Tuple[Optional[dict], List[Attempt]]:
    """Propose, measure, check, and feed any violation back into the next try."""
    attempts: List[Attempt] = []
    feedback = ""
    for i in range(max_rounds):
        choice = propose(feedback)
        if choice is None:
            break
        values, violations = measure(choice)
        att = Attempt(round=i + 1, choice=choice, values=values, violations=violations)
        attempts.append(att)
        if att.ok:
            return choice, attempts
        feedback = "\n".join(f"- {v}" for v in violations[:6])
    return None, attempts


def anchor_maps_to(axis, position: int, expected: float, what: str,
                   rel_slack: float = 0.06, log: bool = False) -> List[str]:
    """A position whose value is known independently must map back to it.

    This is the check that separates a correct calibration from a plausible one.
    A forest plot draws its null line at hazard ratio 1 and a PK plot labels its
    threshold, so those positions carry a value that was never inferred. Bounds
    checks cannot do this job: a wrong axis still yields hazard ratios that look
    like hazard ratios.
    """
    try:
        got = axis.to_value(position)
    except (ValueError, ZeroDivisionError):
        return [f"{what} could not be evaluated under this calibration"]
    if got is None or expected is None:
        return []
    if log:
        if got <= 0:
            return [f"{what} maps to {got}, which is not a positive concentration"]
        off = abs(math.log10(got) - math.log10(expected))
        ok = off <= 0.12
    else:
        ok = abs(got - expected) <= max(rel_slack, abs(expected) * rel_slack)
    return [] if ok else [
        f"{what} maps to {got:.4g} under this calibration but is known to be {expected:.4g}"]


def is_decaying(series_values: Sequence[float], slack: float = 1.25) -> bool:
    """A PK concentration curve falls after its peak rather than climbing."""
    vals = [v for v in series_values if v and v > 0]
    if len(vals) < 3:
        return True
    return vals[-1] <= vals[0] * slack


# --- variants that address how the first loop failed ------------------------

def directional(got: float, want: float, what: str, axis_desc: str) -> str:
    """Say which way to move, not just that the value is wrong.

    A rejection reading "the curve begins at 0.82" tells the model nothing about
    where to go next, and three rounds went 53, 40, 53 without converging. Which
    direction the error implies is derivable, so it should be stated.
    """
    if got < want:
        return (f"{what} came out {got:.3g}, below the {want:.3g} it must be. "
                f"The position you chose for {want:.3g} sits too far {axis_desc[0]}; "
                f"choose one further {axis_desc[1]}.")
    return (f"{what} came out {got:.3g}, above the {want:.3g} it must be. "
            f"The position you chose for {want:.3g} sits too far {axis_desc[1]}; "
            f"choose one further {axis_desc[0]}.")


def lands_on_round_values(axis, positions: Sequence[int], exclude: Sequence[int] = (),
                          tol: float = 0.06) -> List[str]:
    """Independently-found ticks should map to round numbers under a good axis.

    This is the non-circular form of an anchor check. Asking whether the model's
    nominated anchor maps to the value the model gave it is no test at all: it
    holds by construction, which is how a forest calibration at roughly twice the
    true scale was accepted. Positions CV found on its own were never chosen to
    fit, so whether they land on round values is real evidence.
    """
    spare = [p for p in positions if all(abs(p - e) > 4 for e in exclude)]
    if len(spare) < 2:
        return []
    off = []
    for p in spare:
        v = axis.to_value(p)
        nearest = round(v * 2) / 2          # halves are round enough for an axis
        if abs(v - nearest) > tol * max(1.0, abs(nearest)):
            off.append(f"{p}->{v:.3g}")
    if len(off) > len(spare) / 2:
        return [f"most independently-detected ticks do not land on round axis values "
                f"under this calibration ({', '.join(off[:4])}), so the scale is wrong"]
    return []


def decades_are_even(axis, rows: Sequence[int], tol: float = 0.15) -> List[str]:
    """On a log axis, consecutive detected rules should be whole decades apart."""
    if len(rows) < 3:
        return []
    vals = []
    for r in sorted(rows):
        v = axis.to_value(r)
        if v is None or v <= 0:
            return [f"row {r} maps to {v}, which is not a positive concentration"]
        vals.append(math.log10(v))
    gaps = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
    bad = [g for g in gaps if abs(g - round(g)) > tol or round(g) == 0]
    if len(bad) > len(gaps) / 2:
        return [f"consecutive detected rules are {', '.join(f'{g:.2f}' for g in gaps[:4])} "
                f"decades apart, but a log grid spaces them by whole decades, so the "
                f"vertical scale is wrong"]
    return []


_INTERVAL = re.compile(r"^(-?\d+\.\d+)\s+\((-?\d+\.\d+)-(-?\d+\.\d+)\)$")


def _intervals(raw: Dict[str, str], prefix: str = "hazard_ratio"):
    out = []
    for key, text in raw.items():
        if not key.startswith(prefix):
            continue
        m = _INTERVAL.match(str(text).strip())
        if m:
            out.append((key, float(m.group(1)), float(m.group(2)), float(m.group(3))))
    return out


def interval_bounds(raw: Dict[str, str], max_bad_frac: float = 0.15) -> List[str]:
    """A hazard ratio interval is positive at both ends and brackets its estimate.

    The wrong forest calibration emitted 0.57 (-0.00-0.87) and was accepted,
    because the value is a formatted string and only the leading number was ever
    parsed out of it. A ratio of two positive rates cannot be negative, so the
    interval carries a violation that needs no tick, no answer key and no second
    opinion. It was sitting in the output the whole time.

    Judged as a fraction of rows rather than row by row. A single row can fail for
    reasons that have nothing to do with the axis: row detection picks up a band
    of legend text, and that one spurious row then vetoes a calibration the other
    twenty-one agree on. A wrong axis corrupts every row at once, which is what
    distinguishes it from a bad row.
    """
    rows = _intervals(raw)
    if not rows:
        return []
    neg = [f"{k} ({lo:.2f})" for k, _, lo, _ in rows if lo <= 0]
    unbracketed = [k for k, p, lo, hi in rows if not (lo <= p <= hi)]
    limit = max(1, int(len(rows) * max_bad_frac))
    bad = []
    if len(neg) > limit:
        bad.append(f"{len(neg)} of {len(rows)} rows have an interval lower bound at or below "
                   f"zero ({', '.join(neg[:4])}). A hazard ratio is a ratio of positive rates "
                   f"and cannot reach zero, so the horizontal scale is stretched too wide: the "
                   f"two columns you chose are closer together than the values you assigned "
                   f"them imply. Choose columns further apart, or values closer together.")
    if len(unbracketed) > limit:
        bad.append(f"{len(unbracketed)} of {len(rows)} rows have an interval that does not "
                   f"contain its own point estimate ({', '.join(unbracketed[:4])})")
    return bad


# Tried and rejected: counting subgroups that share an interval endpoint, on the
# theory that a tie means whiskers hitting the edge of the search window. Ties at
# two decimals are ordinary in twenty rows of real data, and the check fired on
# the hand calibration as readily as on the wrong one. A check that cannot tell
# those apart is worse than none, because its verdict is fed back and steers the
# next attempt away from the answer.


def axis_contains_data(lo_px: int, hi_px: int, data_lo: int, data_hi: int,
                       what: str, slack: float = 0.25,
                       max_ratio: Optional[float] = 4.0) -> List[str]:
    """A chart's axis spans its own plotted data.

    True of every chart ever drawn, and checkable without reading a single label,
    because where the series colours appear is measured independently of anything
    the model nominated. One proposal put the x axis across 21 pixels of a figure
    whose curves run for 600, and nothing in the loop objected.

    Deliberately loose. Even a trimmed colour extent is a noisy read of where the
    data is, and at a tight tolerance this rejected a Kaplan-Meier axis that was
    fifteen pixels off, which is inside the noise. It is here to catch 21 pixels
    against 600, not to adjudicate 137 against 152, and a check asked to do more
    than it can starts throwing away correct answers.
    """
    if hi_px <= lo_px or data_hi <= data_lo:
        return []
    span, data = hi_px - lo_px, data_hi - data_lo
    pad = slack * data
    if data_lo < lo_px - pad or data_hi > hi_px + pad:
        return [f"the {what} you chose runs from {lo_px} to {hi_px}, but the plotted series "
                f"occupy {data_lo} to {data_hi}. An axis spans its own data, so these are "
                f"not the ends of the axis."]
    if max_ratio is not None and span > data * max_ratio:
        return [f"the {what} you chose runs from {lo_px} to {hi_px}, four times wider than "
                f"the {data_lo} to {data_hi} the plotted series occupy. You have most likely "
                f"picked page furniture rather than the axis."]
    return []


def best_of(propose: Callable[[str], Optional[dict]], measure, n: int = 3):
    """Sample several proposals and keep the first that survives its checks.

    The same figure produced a correct calibration in one run and three failures
    in the next, so a single sample is not a reliable read of what the model can do.
    """
    attempts = []
    for i in range(n):
        choice = propose("")
        if choice is None:
            continue
        values, violations = measure(choice)
        attempts.append(Attempt(round=i + 1, choice=choice, values=values,
                                violations=violations))
        if not violations:
            return choice, attempts
    return None, attempts
