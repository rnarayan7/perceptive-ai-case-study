"""Aggregate scored values into the report.

Deterministic rollups of a list of :class:`ScoreResult`: error by figure type and
family, proportion and set accuracy, interval coverage, a calibration curve with
its expected calibration error, the keystone (does read-agreement predict error),
and an error-category histogram. ``format_report`` renders it for the terminal.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from evaluation.types import Family, ScoreResult

# Confidence bins for the reliability curve.
_BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01)]


def aggregate(results: List[ScoreResult], missed: int = 0) -> Dict[str, Any]:
    scored = [r for r in results if r.family != Family.INTERPRETIVE]
    interpretive = [r for r in results if r.family == Family.INTERPRETIVE]

    report: Dict[str, Any] = {
        "counts": {
            "total": len(results),
            "scored": len(scored),
            "interpretive": len(interpretive),
            "missed": missed,
        },
        "by_figure_type": _by_group(scored, key=lambda r: r.figure_type),
        "by_family": _by_group(scored, key=lambda r: r.family),
        "coverage": _coverage(scored),
        "calibration": _calibration(scored),
        "agreement_vs_error": _agreement_vs_error(scored),
        "error_categories": _error_categories(scored),
    }
    return report


def _by_group(results, key) -> Dict[str, Any]:
    groups: Dict[str, List[ScoreResult]] = {}
    for r in results:
        groups.setdefault(key(r), []).append(r)
    out: Dict[str, Any] = {}
    for name, items in sorted(groups.items()):
        errors = [r.error for r in items if r.error is not None]
        within = [r.within_tolerance for r in items if r.within_tolerance is not None]
        out[name] = {
            "n": len(items),
            "mae": _mean(errors),
            "median_error": _median(errors),
            "within_tolerance_rate": _mean([1.0 if w else 0.0 for w in within]) if within else None,
        }
    return out


def _coverage(results) -> Optional[float]:
    flags = [r.interval_covers_truth for r in results if r.interval_covers_truth is not None]
    return _mean([1.0 if f else 0.0 for f in flags]) if flags else None


def _calibration(results) -> Dict[str, Any]:
    """Reliability curve plus expected calibration error.

    Correctness is ``within_tolerance``; confidence is the stated confidence.
    ECE weights each bin's |mean confidence - accuracy| by its share of values.
    """
    usable = [r for r in results if r.confidence is not None and r.within_tolerance is not None]
    if not usable:
        return {"bins": [], "ece": None, "n": 0}
    bins = []
    ece = 0.0
    for lo, hi in _BINS:
        members = [r for r in usable if lo <= r.confidence < hi]
        if not members:
            continue
        conf = _mean([r.confidence for r in members])
        acc = _mean([1.0 if r.within_tolerance else 0.0 for r in members])
        bins.append({"range": [lo, hi], "n": len(members),
                     "mean_confidence": round(conf, 3), "accuracy": round(acc, 3)})
        ece += (len(members) / len(usable)) * abs(conf - acc)
    return {"bins": bins, "ece": round(ece, 3), "n": len(usable)}


def _agreement_vs_error(results) -> Dict[str, Any]:
    """The keystone: correlate dual-read agreement (spread) with actual error.

    A positive correlation means divergence predicts error, which licenses using
    read-disagreement as an unlabeled triage signal in production.
    """
    pairs = [(r.agreement, r.error) for r in results
             if r.agreement is not None and r.error is not None]
    if len(pairs) < 3:
        return {"pearson_r": None, "n": len(pairs)}
    xs, ys = zip(*pairs)
    return {"pearson_r": _round(_pearson(xs, ys)), "n": len(pairs)}


def _error_categories(results) -> Dict[str, int]:
    hist: Dict[str, int] = {}
    for r in results:
        if r.error_category and r.error_category != "ok":
            hist[r.error_category] = hist.get(r.error_category, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: -kv[1]))


# --- small stats helpers ---------------------------------------------------

def _mean(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _median(xs) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    mid = len(xs) // 2
    return round(xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2, 4)


def _pearson(xs, ys) -> Optional[float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _round(x: Optional[float]) -> Optional[float]:
    return round(x, 3) if x is not None else None


def format_report(report: Dict[str, Any]) -> str:
    c = report["counts"]
    lines = [
        "=" * 60,
        f"Figure-extraction eval: {c['scored']} scored, "
        f"{c['interpretive']} interpretive, {c['missed']} missed",
        "=" * 60,
        "",
        "By figure type:",
    ]
    for name, s in report["by_figure_type"].items():
        wt = f"{s['within_tolerance_rate']:.2f}" if s["within_tolerance_rate"] is not None else "  - "
        mae = f"{s['mae']:.3f}" if s["mae"] is not None else "  -  "
        lines.append(f"  {name:14s} n={s['n']:<3d} within_tol={wt}  MAE={mae}")

    cal = report["calibration"]
    lines += ["", f"Calibration (ECE={cal['ece']}, n={cal['n']}):"]
    for b in cal["bins"]:
        lines.append(f"  conf {b['range'][0]:.2f}-{b['range'][1]:.2f}: "
                     f"stated={b['mean_confidence']:.2f} actual={b['accuracy']:.2f} (n={b['n']})")

    ave = report["agreement_vs_error"]
    lines += ["", f"Keystone (read-agreement vs error): pearson_r={ave['pearson_r']} (n={ave['n']})"]

    cov = report["coverage"]
    lines += ["", f"Interval coverage: {cov if cov is not None else 'n/a'}"]

    if report["error_categories"]:
        lines += ["", "Error categories: " +
                  ", ".join(f"{k}={v}" for k, v in report["error_categories"].items())]
    lines.append("=" * 60)
    return "\n".join(lines)
