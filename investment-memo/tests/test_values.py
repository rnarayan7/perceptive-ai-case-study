"""Regression tests for parsing numbers out of module claim values.

These use the real value formats the peak_sales and pos modules emit. They guard the
two bugs the pre-run check found: a range hyphen read as a negative sign, and peak-sales
picking a per-unit price claim instead of the headline magnitude.
"""

from __future__ import annotations

import pytest

from memo.analysis.base import AnalysisResult, Claim
from memo.compose.values import parse_numeric, peak_sales_usd, pos_fraction


def _result(module, values):
    return AnalysisResult(
        company="TEST", module=module, summary="",
        claims=[Claim(statement="c", confidence=0.3, rationale="", value=v) for v in values],
    )


def test_peak_sales_range_midpoint():
    # "$0.4-0.9B" -> midpoint 0.65B, not -0.25B (the hyphen is a separator).
    assert peak_sales_usd(_result("peak_sales", ["$0.4-0.9B"])) == pytest.approx(0.65e9)


def test_peak_sales_single_magnitude():
    assert peak_sales_usd(_result("peak_sales", ["$1.2B"])) == pytest.approx(1.2e9)
    assert peak_sales_usd(_result("peak_sales", ["$500M"])) == pytest.approx(5e8)


def test_peak_sales_skips_per_unit_price_claim():
    # A per-unit price claim before the headline must be ignored (no B/M/K suffix).
    got = peak_sales_usd(_result("peak_sales", ["$41.2 per dosage unit", "$0.4-0.9B"]))
    assert got == pytest.approx(0.65e9)


def test_peak_sales_none_when_no_magnitude():
    assert peak_sales_usd(_result("peak_sales", ["$41.2 per dosage unit"])) is None
    assert peak_sales_usd(_result("peak_sales", [])) is None


def test_pos_band_midpoint():
    # "35-50%" -> 0.425, not 0.0 (the hyphen is a separator, not a minus).
    assert pos_fraction(_result("pos", ["35-50%"])) == pytest.approx(0.425)


def test_pos_single_and_fraction_forms():
    assert pos_fraction(_result("pos", ["40%"])) == pytest.approx(0.40)
    assert pos_fraction(_result("pos", ["0.45"])) == pytest.approx(0.45)
    assert pos_fraction(_result("pos", ["150%"])) == pytest.approx(1.0)  # clamped


def test_parse_numeric_forms():
    # 0b: (number, unit) for ClaimRecord.value_num / value_unit.
    n, u = parse_numeric("$0.4-0.9B")
    assert n == pytest.approx(0.65e9) and u == "USD"
    n, u = parse_numeric("$500M")
    assert n == pytest.approx(5e8) and u == "USD"
    n, u = parse_numeric("35-50%")
    assert n == pytest.approx(42.5) and u == "%"
    n, u = parse_numeric("$58.00")
    assert n == pytest.approx(58.0) and u == "USD"
    n, u = parse_numeric("0.62")
    assert n == pytest.approx(0.62) and u is None
    # A "(0-1)" scale annotation must not pull the midpoint to 0.5.
    n, u = parse_numeric("0.3 fraction (0-1)")
    assert n == pytest.approx(0.3) and u is None
    n, u = parse_numeric("0.12 probability (0-1)")
    assert n == pytest.approx(0.12) and u is None
    assert parse_numeric(None) == (None, None)
    assert parse_numeric("not reached") == (None, None)
