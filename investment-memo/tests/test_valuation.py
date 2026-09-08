"""Tests for the rNPV / valuation module.

The module's discipline mirrors peak-sales: all arithmetic is a pure, deterministic
function, so the tests carry the weight by pinning it to hand-computed curves. No network
and no model calls are involved.
"""

from __future__ import annotations

import pytest

from memo.analysis.valuation import compute_rnpv, rnpv_claim


# ----------------------------------------------------------- exact arithmetic


def test_exact_arithmetic_undiscounted():
    # peak 1000, one ramp year (-> 1000), one plateau year (1000), one decline year (0).
    # No discount, full margin, no pre-launch delay: curve [1000, 1000, 0].
    out = compute_rnpv(
        peak_sales_usd=1000.0,
        pos=0.5,
        years_to_peak=0,
        discount_rate=0.0,
        ramp_years=1,
        plateau_years=1,
        decline_years=1,
        operating_margin=1.0,
    )
    assert out["revenue_curve"] == [1000.0, 1000.0, 0.0]
    assert out["cash_flow_curve"] == [1000.0, 1000.0, 0.0]
    assert out["unadjusted_npv"] == 2000.0
    assert out["rnpv"] == 1000.0
    assert out["pos"] == 0.5


def test_exact_arithmetic_with_margin_and_launch_delay():
    # Two pre-launch years, 2y ramp (500, 1000), 2y plateau (1000, 1000),
    # 2y decline (500, 0), 50% margin, no discount.
    out = compute_rnpv(
        peak_sales_usd=1000.0,
        pos=1.0,
        years_to_peak=2,
        discount_rate=0.0,
        ramp_years=2,
        plateau_years=2,
        decline_years=2,
        operating_margin=0.5,
    )
    assert out["revenue_curve"] == [0.0, 0.0, 500.0, 1000.0, 1000.0, 1000.0, 500.0, 0.0]
    assert out["cash_flow_curve"] == [0.0, 0.0, 250.0, 500.0, 500.0, 500.0, 250.0, 0.0]
    assert out["unadjusted_npv"] == 2000.0
    assert out["rnpv"] == 2000.0


def test_exact_arithmetic_with_discount():
    # curve [1000, 1000, 0], full margin, discounted at 10% end-of-year:
    #   1000/1.1 + 1000/1.21 = 909.0909... + 826.4462... = 1735.5372...
    out = compute_rnpv(
        peak_sales_usd=1000.0,
        pos=1.0,
        years_to_peak=0,
        discount_rate=0.1,
        ramp_years=1,
        plateau_years=1,
        decline_years=1,
        operating_margin=1.0,
    )
    expected = 1000.0 / 1.1 + 1000.0 / 1.21
    assert out["unadjusted_npv"] == pytest.approx(expected)
    assert out["rnpv"] == pytest.approx(expected)


# ----------------------------------------------------------- core relationships


def test_rnpv_is_unadjusted_times_pos():
    out = compute_rnpv(peak_sales_usd=2_000_000_000.0, pos=0.35)
    assert out["rnpv"] == pytest.approx(out["unadjusted_npv"] * 0.35)
    assert out["pos"] == 0.35


def test_higher_discount_rate_lowers_rnpv():
    low = compute_rnpv(peak_sales_usd=1_000_000_000.0, pos=0.5, discount_rate=0.08)
    high = compute_rnpv(peak_sales_usd=1_000_000_000.0, pos=0.5, discount_rate=0.20)
    assert high["rnpv"] < low["rnpv"]
    assert low["rnpv"] > 0


# ----------------------------------------------------------------- guardrails


def test_pos_zero_gives_zero_rnpv_with_note():
    out = compute_rnpv(peak_sales_usd=1_000_000_000.0, pos=0.0)
    assert out["rnpv"] == 0.0
    assert out["unadjusted_npv"] > 0  # the unadjusted stream still has value
    assert "pos is 0" in out["note"]


def test_pos_is_clamped_to_unit_interval():
    hi = compute_rnpv(peak_sales_usd=1_000_000_000.0, pos=1.7)
    lo = compute_rnpv(peak_sales_usd=1_000_000_000.0, pos=-0.4)
    assert hi["pos"] == 1.0
    assert hi["rnpv"] == pytest.approx(hi["unadjusted_npv"])
    assert lo["pos"] == 0.0
    assert lo["rnpv"] == 0.0


def test_non_positive_peak_gives_zero_rnpv_and_zero_curve():
    out = compute_rnpv(peak_sales_usd=0.0, pos=0.5)
    assert out["rnpv"] == 0.0
    assert out["unadjusted_npv"] == 0.0
    assert all(v == 0.0 for v in out["revenue_curve"])
    assert "peak_sales_usd <= 0" in out["note"]

    neg = compute_rnpv(peak_sales_usd=-500.0, pos=0.5)
    assert neg["rnpv"] == 0.0
    assert all(v == 0.0 for v in neg["revenue_curve"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"years_to_peak": -1},
        {"ramp_years": -2},
        {"plateau_years": -1},
        {"decline_years": -3},
        {"discount_rate": -1.0},
    ],
)
def test_invalid_params_raise(kwargs):
    with pytest.raises(ValueError):
        compute_rnpv(peak_sales_usd=1_000_000_000.0, pos=0.5, **kwargs)


# ----------------------------------------------------------- curve shape/length


def test_curve_length_matches_default_timeline():
    out = compute_rnpv(peak_sales_usd=1_000_000_000.0, pos=0.5)
    # defaults: 3 pre-launch + 4 ramp + 6 plateau + 3 decline = 16 years
    assert len(out["revenue_curve"]) == 3 + 4 + 6 + 3
    assert out["assumptions"]["ramp_years"] == 4
    assert out["assumptions"]["plateau_years"] == 6
    assert out["assumptions"]["decline_years"] == 3


def test_curve_shape_ramp_plateau_decline():
    peak = 1_000_000_000.0
    out = compute_rnpv(
        peak_sales_usd=peak,
        pos=0.5,
        years_to_peak=2,
        ramp_years=3,
        plateau_years=4,
        decline_years=2,
    )
    curve = out["revenue_curve"]

    launch, ramp, plateau, decline = curve[:2], curve[2:5], curve[5:9], curve[9:]

    # pre-launch zeros
    assert launch == [0.0, 0.0]
    # ramp strictly increasing up to peak, ending exactly at peak
    assert ramp[0] < ramp[1] < ramp[2]
    assert ramp[-1] == pytest.approx(peak)
    # plateau flat at peak
    assert plateau == [pytest.approx(peak)] * 4
    # decline strictly decreasing, ending at 0
    assert decline[0] < peak
    assert decline[-1] == pytest.approx(0.0)
    assert decline[0] > decline[-1]


def test_zero_length_segments_are_allowed():
    # No ramp and no decline: straight to a 2-year plateau at peak.
    out = compute_rnpv(
        peak_sales_usd=100.0,
        pos=1.0,
        years_to_peak=0,
        discount_rate=0.0,
        ramp_years=0,
        plateau_years=2,
        decline_years=0,
        operating_margin=1.0,
    )
    assert out["revenue_curve"] == [100.0, 100.0]
    assert out["unadjusted_npv"] == 200.0


# ------------------------------------------------------------------- claim helper


def test_rnpv_claim_carries_value_and_is_ungrounded_without_evidence():
    claim = rnpv_claim(peak_sales_usd=2_000_000_000.0, pos=0.4)
    assert claim.value is not None
    assert claim.value.startswith("$") and claim.value.endswith("B")
    assert not claim.is_grounded  # no evidence passed
    assert "Risk-adjusted NPV" in claim.statement


def test_rnpv_claim_is_grounded_with_evidence():
    from memo.analysis.base import Evidence

    ev = Evidence(
        doc_id="F1", source="edgar", doc_type="10-K",
        url="https://example.com/F1", quote="peak sales guidance",
    )
    claim = rnpv_claim(peak_sales_usd=2_000_000_000.0, pos=0.4, evidence=[ev], confidence=0.5)
    assert claim.is_grounded
    assert claim.confidence == 0.5
