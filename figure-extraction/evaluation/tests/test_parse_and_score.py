"""Unit tests for the deterministic core: parsing and scoring.

These are where correctness lives, so they are tested directly on hand-made cases
rather than only through the stub. Run: ``python -m pytest evaluation/tests`` or
``python evaluation/tests/test_parse_and_score.py`` (no pytest needed).
"""

from __future__ import annotations

from evaluation.parse import parse_value
from evaluation.scorers import score
from evaluation.types import Family, Prediction, QuantitySpec, ValueKind


def _pred(value, key="k", ftype="t", unit=None, interval=None, reads=None, conf=None):
    lo, hi = interval if interval else (None, None)
    return Prediction("f", ftype, key, value, unit=unit, interval_low=lo,
                      interval_high=hi, confidence=conf, reads=reads or {})


# -- parsing ---------------------------------------------------------------

def test_parse_scalar_with_unit():
    v = parse_value("5.5 months", "months", Family.CONTINUOUS)
    assert v.kind == ValueKind.SCALAR and v.scalar == 5.5 and v.unit == "months"


def test_parse_ratio_ci():
    v = parse_value("0.50 (95% CI 0.33-0.72)", None, Family.RATIO_CI)
    assert v.kind == ValueKind.RATIO
    assert v.scalar == 0.5 and v.ci_low == 0.33 and v.ci_high == 0.72


def test_parse_proportion_recovers_denominator():
    v = parse_value("45.9% (39/83)", None, Family.PROPORTION)
    assert v.kind == ValueKind.PROPORTION and v.numerator == 39 and v.denominator == 83


def test_parse_sentinel():
    v = parse_value("not reached", None, Family.CONTINUOUS)
    assert v.kind == ValueKind.SENTINEL and v.sentinel == "not_reached"


def test_parse_set_normalizes():
    v = parse_value("Age over 65, BMI over 30", None, Family.CATEGORICAL_SET)
    assert v.items == frozenset({"age over 65", "bmi over 30"})


# -- scoring ---------------------------------------------------------------

def test_continuous_within_tolerance_and_coverage():
    spec = QuantitySpec("median", Family.CONTINUOUS, "months", 1.0)
    p = parse_value("5.6 months", "months", Family.CONTINUOUS)
    t = parse_value("5.5 months", "months", Family.CONTINUOUS)
    r = score(_pred("5.6 months", unit="months", interval=(5.0, 6.0)), p, t, spec)
    assert r.within_tolerance is True
    assert abs(r.error - 0.1) < 1e-9
    assert r.interval_covers_truth is True


def test_continuous_unit_mismatch_is_flagged():
    spec = QuantitySpec("median", Family.CONTINUOUS, "months", 1.0)
    p = parse_value("5.5 weeks", "weeks", Family.CONTINUOUS)
    t = parse_value("5.5 months", "months", Family.CONTINUOUS)
    r = score(_pred("5.5 weeks", unit="weeks"), p, t, spec)
    assert r.within_tolerance is False and r.error_category == "unit_mismatch"


def test_proportion_wrong_denominator_is_its_own_category():
    spec = QuantitySpec("cr", Family.PROPORTION)
    p = parse_value("45.9% (39/103)", None, Family.PROPORTION)
    t = parse_value("45.9% (39/83)", None, Family.PROPORTION)
    r = score(_pred("45.9% (39/103)"), p, t, spec)
    assert r.within_tolerance is False and r.error_category == "denominator_error"


def test_proportion_exact_match():
    spec = QuantitySpec("cr", Family.PROPORTION)
    p = parse_value("46% (51/110)", None, Family.PROPORTION)
    t = parse_value("46.4% (51/110)", None, Family.PROPORTION)
    r = score(_pred("46% (51/110)"), p, t, spec)
    assert r.within_tolerance is True and r.error_category == "ok"


def test_log_scale_uses_fold_difference():
    spec = QuantitySpec("conc", Family.LOG_SCALE, "nM", 1.6)
    p = parse_value("0.30 nM", "nM", Family.LOG_SCALE)
    t = parse_value("0.25 nM", "nM", Family.LOG_SCALE)
    r = score(_pred("0.30 nM", unit="nM"), p, t, spec)
    assert r.within_tolerance is True  # 1.2x fold, under 1.6
    assert r.details["fold_difference"] < 1.6


def test_ratio_ci_point_and_bounds():
    spec = QuantitySpec("hr", Family.RATIO_CI, None, 0.1)
    p = parse_value("0.52 (95% CI 0.35-0.74)", None, Family.RATIO_CI)
    t = parse_value("0.50 (95% CI 0.33-0.72)", None, Family.RATIO_CI)
    r = score(_pred("0.52 (95% CI 0.35-0.74)"), p, t, spec)
    assert r.within_tolerance is True
    assert r.details["point_error"] == 0.02


def test_categorical_set_precision_recall():
    spec = QuantitySpec("crosses", Family.CATEGORICAL_SET)
    p = parse_value("A, B, C", None, Family.CATEGORICAL_SET)   # C is a false positive
    t = parse_value("A, B, D", None, Family.CATEGORICAL_SET)   # D is missed
    r = score(_pred("A, B, C"), p, t, spec)
    assert r.details["false_positives"] == ["c"]
    assert r.details["false_negatives"] == ["d"]
    assert r.within_tolerance is False


def test_sentinel_match_and_mismatch():
    spec = QuantitySpec("median", Family.CONTINUOUS, "months", 1.0)
    both = score(_pred("not reached"),
                 parse_value("not reached", None, Family.CONTINUOUS),
                 parse_value("not reached", None, Family.CONTINUOUS), spec)
    assert both.within_tolerance is True
    mixed = score(_pred("12 months", unit="months"),
                  parse_value("12 months", "months", Family.CONTINUOUS),
                  parse_value("not reached", None, Family.CONTINUOUS), spec)
    assert mixed.within_tolerance is False and mixed.error_category == "sentinel_mismatch"


def test_interpretive_is_not_scored():
    spec = QuantitySpec("rule", Family.INTERPRETIVE)
    r = score(_pred("some rule"), parse_value("some rule"), parse_value("some rule"), spec)
    assert r.error is None and r.error_category == "interpretive"


def test_proportion_slop_tolerance():
    spec = QuantitySpec("psa", Family.PROPORTION, None, 2)  # measured off a chart
    # off by one bar/patient on the numerator -> within slop, passes
    r = score(_pred("72% (61/85)"), parse_value("72% (61/85)", None, Family.PROPORTION),
              parse_value("73% (62/85)", None, Family.PROPORTION), spec)
    assert r.within_tolerance is True
    # off by one on the denominator -> within slop, passes
    r2 = score(_pred("31% (8/26)"), parse_value("31% (8/26)", None, Family.PROPORTION),
               parse_value("30% (8/27)", None, Family.PROPORTION), spec)
    assert r2.within_tolerance is True
    # a genuinely wrong denominator (the fig2 trap) is still a denominator_error
    r3 = score(_pred("9% (8/85)"), parse_value("9% (8/85)", None, Family.PROPORTION),
               parse_value("30% (8/27)", None, Family.PROPORTION), spec)
    assert r3.within_tolerance is False and r3.error_category == "denominator_error"


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
