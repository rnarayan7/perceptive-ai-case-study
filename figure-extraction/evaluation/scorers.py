"""Deterministic numeric scorers, one per value family.

Every scorer is a pure function of two parsed values plus the prediction (for its
interval and reads) and the spec (for tolerance). No model is involved: the score
is arithmetic, so it is exact, reproducible, and defensible. Interpretive values
are the sole exception and are not scored here.

Dispatch through :data:`SCORERS` by family. Each returns a :class:`ScoreResult`
carrying an error, a within-tolerance flag, whether the reported interval covered
truth, and an ``error_category`` naming the kind of miss.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, Optional

from evaluation.types import (
    Family,
    ParsedValue,
    Prediction,
    QuantitySpec,
    ScoreResult,
    ValueKind,
)

Scorer = Callable[[Prediction, ParsedValue, ParsedValue, QuantitySpec], ScoreResult]


def _base(pred: Prediction, spec: QuantitySpec, p: ParsedValue, t: ParsedValue) -> ScoreResult:
    return ScoreResult(
        figure_id=pred.figure_id,
        figure_type=pred.figure_type,
        quantity_key=spec.key,
        family=spec.family,
        predicted_raw=p.raw,
        truth_raw=t.raw,
        confidence=pred.confidence,
        agreement=_read_agreement(pred),
    )


def _read_agreement(pred: Prediction) -> Optional[float]:
    """Spread between the independent reads, when at least two are present.

    Works for any two-or-more read scheme (VLM + CV, or two VLM samples): the
    spread is max minus min over the numeric reads. Fewer than two reads means
    no agreement signal for this value.
    """
    values = [v for v in pred.reads.values() if v is not None]
    if len(values) < 2:
        return None
    return max(values) - min(values)


def _sentinel_guard(res: ScoreResult, p: ParsedValue, t: ParsedValue) -> Optional[ScoreResult]:
    """Handle sentinel ("not reached") on either side before numeric scoring."""
    p_sent = p.kind == ValueKind.SENTINEL
    t_sent = t.kind == ValueKind.SENTINEL
    if not p_sent and not t_sent:
        return None
    if p_sent and t_sent:
        res.within_tolerance = p.sentinel == t.sentinel
        res.error_category = "ok" if res.within_tolerance else "sentinel_mismatch"
    else:
        res.within_tolerance = False
        res.error_category = "sentinel_mismatch"
    return res


def score_continuous(pred, p, t, spec) -> ScoreResult:
    res = _base(pred, spec, p, t)
    guarded = _sentinel_guard(res, p, t)
    if guarded is not None:
        return guarded
    if p.scalar is None or t.scalar is None:
        res.error_category = "unparsed"
        res.within_tolerance = False
        return res
    if _unit_conflict(p.unit, t.unit, spec.unit):
        res.error_category = "unit_mismatch"
        res.within_tolerance = False
        res.details = {"pred_unit": p.unit, "truth_unit": t.unit}
        return res
    res.error = abs(p.scalar - t.scalar)
    res.rel_error = res.error / abs(t.scalar) if t.scalar else None
    if spec.tolerance is not None:
        res.within_tolerance = res.error <= spec.tolerance
    res.interval_covers_truth = _covers(pred, t.scalar)
    res.error_category = "ok" if res.within_tolerance else "value_error"
    return res


def score_log_scale(pred, p, t, spec) -> ScoreResult:
    """Score on a log axis: error is in log10, tolerance is a fold-difference."""
    res = _base(pred, spec, p, t)
    guarded = _sentinel_guard(res, p, t)
    if guarded is not None:
        return guarded
    if not p.scalar or not t.scalar or p.scalar <= 0 or t.scalar <= 0:
        res.error_category = "unparsed"
        res.within_tolerance = False
        return res
    log_err = abs(math.log10(p.scalar) - math.log10(t.scalar))
    res.error = log_err
    res.rel_error = 10 ** log_err  # fold-difference, the interpretable quantity
    if spec.tolerance is not None:
        res.within_tolerance = res.rel_error <= spec.tolerance
    res.interval_covers_truth = _covers(pred, t.scalar)
    res.error_category = "ok" if res.within_tolerance else "value_error"
    res.details = {"fold_difference": round(res.rel_error, 3)}
    return res


def score_proportion(pred, p, t, spec) -> ScoreResult:
    """Match numerator AND denominator, within ``spec.tolerance`` slop.

    ``spec.tolerance`` is an allowed absolute difference (in patients/bars) on both
    the numerator and the denominator: 0 (the default when unset) is exact match,
    right for transcribed table cells; a small slop (e.g. 2) is right for a
    proportion *measured* off a chart, where the last bar or two are at the pixel
    resolution limit. The slop stays small, so a genuinely wrong denominator (the
    trap fig 2 sets, off by tens) still fails as a ``denominator_error``.
    """
    res = _base(pred, spec, p, t)
    if t.kind != ValueKind.PROPORTION or t.denominator is None:
        res.error_category = "gold_not_proportion"
        return res
    if p.kind != ValueKind.PROPORTION or p.denominator is None:
        res.within_tolerance = False
        res.error_category = "missing_denominator"
        return res
    slop = int(spec.tolerance) if spec.tolerance else 0
    num_ok = abs(p.numerator - t.numerator) <= slop
    den_ok = abs(p.denominator - t.denominator) <= slop
    res.within_tolerance = num_ok and den_ok
    if den_ok and not num_ok:
        res.error_category = "numerator_error"
    elif not den_ok:
        res.error_category = "denominator_error"
    else:
        res.error_category = "ok"
    # Secondary continuous error on the implied percentage, for reporting.
    p_pct = _pct(p)
    t_pct = _pct(t)
    if p_pct is not None and t_pct is not None:
        res.error = abs(p_pct - t_pct)
    res.details = {"pred": f"{p.numerator}/{p.denominator}", "truth": f"{t.numerator}/{t.denominator}"}
    return res


def score_ratio_ci(pred, p, t, spec) -> ScoreResult:
    """Point-estimate error plus CI-bound errors (forest-plot hazard ratios)."""
    res = _base(pred, spec, p, t)
    if p.scalar is None or t.scalar is None:
        res.error_category = "unparsed"
        res.within_tolerance = False
        return res
    res.error = abs(p.scalar - t.scalar)
    res.rel_error = res.error / abs(t.scalar) if t.scalar else None
    tol = spec.tolerance if spec.tolerance is not None else 0.1
    point_ok = res.error <= tol
    lo_err = _bound_err(p.ci_low, t.ci_low)
    hi_err = _bound_err(p.ci_high, t.ci_high)
    ci_ok = all(e is None or e <= tol for e in (lo_err, hi_err))
    res.within_tolerance = point_ok and ci_ok
    res.interval_covers_truth = _covers(pred, t.scalar)
    res.error_category = "ok" if res.within_tolerance else ("point_error" if not point_ok else "ci_error")
    res.details = {"point_error": round(res.error, 3),
                   "ci_low_error": _round(lo_err), "ci_high_error": _round(hi_err)}
    return res


def score_fold_multiple(pred, p, t, spec) -> ScoreResult:
    """Relative error on a dimensionless fold-multiple."""
    res = _base(pred, spec, p, t)
    if not p.scalar or not t.scalar:
        res.error_category = "unparsed"
        res.within_tolerance = False
        return res
    res.error = abs(p.scalar - t.scalar)
    res.rel_error = res.error / abs(t.scalar) if t.scalar else None
    tol = spec.tolerance if spec.tolerance is not None else 0.5
    res.within_tolerance = res.rel_error is not None and res.rel_error <= tol
    res.error_category = "ok" if res.within_tolerance else "value_error"
    return res


def score_categorical_set(pred, p, t, spec) -> ScoreResult:
    """Precision / recall / F1 against the true set; error is 1 - F1."""
    res = _base(pred, spec, p, t)
    pred_set = p.items or frozenset()
    truth_set = t.items or frozenset()
    tp = len(pred_set & truth_set)
    fp = len(pred_set - truth_set)
    fn = len(truth_set - pred_set)
    precision = tp / (tp + fp) if (tp + fp) else (1.0 if not truth_set else 0.0)
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    res.error = 1.0 - f1
    res.within_tolerance = f1 >= 0.999
    res.error_category = "ok" if res.within_tolerance else "set_mismatch"
    res.details = {
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "false_positives": sorted(pred_set - truth_set),
        "false_negatives": sorted(truth_set - pred_set),
    }
    return res


SCORERS: Dict[str, Scorer] = {
    Family.CONTINUOUS: score_continuous,
    Family.LOG_SCALE: score_log_scale,
    Family.PROPORTION: score_proportion,
    Family.RATIO_CI: score_ratio_ci,
    Family.FOLD_MULTIPLE: score_fold_multiple,
    Family.CATEGORICAL_SET: score_categorical_set,
}


def score(pred: Prediction, p: ParsedValue, t: ParsedValue, spec: QuantitySpec) -> ScoreResult:
    """Dispatch to the family scorer. Interpretive values are returned unscored."""
    if spec.family == Family.INTERPRETIVE:
        res = _base(pred, spec, p, t)
        res.error_category = "interpretive"
        return res
    scorer = SCORERS.get(spec.family)
    if scorer is None:
        res = _base(pred, spec, p, t)
        res.error_category = "no_scorer"
        return res
    return scorer(pred, p, t, spec)


# --- helpers ---------------------------------------------------------------

def _covers(pred: Prediction, truth: float) -> Optional[bool]:
    if pred.interval_low is None or pred.interval_high is None:
        return None
    lo, hi = sorted((pred.interval_low, pred.interval_high))
    return lo <= truth <= hi


def _unit_conflict(pred_unit: Optional[str], truth_unit: Optional[str], spec_unit: Optional[str]) -> bool:
    """True only when both sides state a unit and they disagree.

    Missing units are tolerated (many reads omit them); ``probability`` and
    ``count`` are pseudo-units that never appear in a value string.
    """
    def norm(u: Optional[str]) -> Optional[str]:
        if not u or u in ("probability", "count"):
            return None
        return u.lower().rstrip("s")
    pu, tu = norm(pred_unit), norm(truth_unit)
    return pu is not None and tu is not None and pu != tu


def _pct(v: ParsedValue) -> Optional[float]:
    if v.scalar is not None:
        return v.scalar
    if v.numerator is not None and v.denominator:
        return 100.0 * v.numerator / v.denominator
    return None


def _bound_err(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return abs(a - b)


def _round(x: Optional[float]) -> Optional[float]:
    return round(x, 3) if x is not None else None
