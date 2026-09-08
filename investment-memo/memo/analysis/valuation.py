"""Risk-adjusted NPV (rNPV): the model supplies the drivers, Python does the math.

A drug's value is the present value of the cash it will throw off, discounted for the
time value of money and multiplied by the probability it ever reaches the market. As with
:mod:`memo.analysis.peak_sales`, letting a language model do that discounting invites
silent errors and unauditable point numbers, so the arithmetic lives here in a pure,
deterministic function that a composition engine calls with a peak-sales figure and a
probability of success.

Method (deliberately standard and transparent):

- Build an annual revenue curve on a simple launch -> ramp -> plateau -> decline shape.
  ``years_to_peak`` years of pre-launch zeros, then a linear ramp up to ``peak_sales_usd``
  over ``ramp_years`` (the last ramp year hits peak), a flat plateau at peak for
  ``plateau_years``, then a linear decline back to zero over ``decline_years`` (loss of
  exclusivity / generic erosion; the last decline year is zero).
- Multiply revenue by ``operating_margin`` to get a net cash flow. This is a single
  blended margin standing in for COGS, SG&A, R&D and tax; it is an explicit assumption,
  echoed back in the result, not a grounded number.
- Discount each year's cash flow to present value at ``discount_rate`` with end-of-year
  discounting (year *t* is divided by ``(1 + discount_rate) ** t``), sum to an unadjusted
  NPV, then multiply by ``pos`` for the risk-adjusted rNPV.

Every number in the returned dict is derived in code from the echoed assumptions, so the
whole calculation is auditable.
"""

from __future__ import annotations

from typing import Any, Dict, List

from memo.analysis.base import Claim, Evidence

# A single blended operating margin standing in for the full cost structure (COGS, SG&A,
# R&D, tax) that turns top-line revenue into net cash flow. It is an explicit modelling
# assumption, not a grounded figure, and is surfaced in the result assumptions and note.
_DEFAULT_OPERATING_MARGIN = 0.35


def compute_rnpv(
    peak_sales_usd: float,
    pos: float,
    years_to_peak: int = 3,
    discount_rate: float = 0.12,
    ramp_years: int = 4,
    plateau_years: int = 6,
    decline_years: int = 3,
    operating_margin: float = _DEFAULT_OPERATING_MARGIN,
) -> Dict[str, Any]:
    """Compute a risk-adjusted NPV of a drug's revenue stream. Pure and deterministic.

    Builds a launch -> ramp -> plateau -> decline annual revenue curve, converts it to net
    cash flow via ``operating_margin``, discounts each year to present value at
    ``discount_rate`` (end-of-year), sums to ``unadjusted_npv`` and multiplies by ``pos``
    for ``rnpv``.

    Timeline (all lengths in whole years, each may be zero):

    - years ``1 .. years_to_peak``: pre-launch, revenue 0
    - next ``ramp_years`` years: linear ramp, year *i* of the ramp = ``peak * i/ramp_years``
      so the final ramp year equals ``peak``
    - next ``plateau_years`` years: flat at ``peak``
    - next ``decline_years`` years: linear decline, year *i* = ``peak * (1 - i/decline_years)``
      so the final decline year is 0

    The returned ``revenue_curve`` is the full per-year curve including the leading
    pre-launch zeros, so its index lines up one-to-one with the discount year *t*.

    Guardrails: ``pos`` is clamped to ``[0, 1]``. If ``peak_sales_usd <= 0`` or the clamped
    ``pos`` is 0, ``rnpv`` is 0 and a ``note`` says why (with a peak <= 0 the curve is all
    zeros). Negative year parameters or a discount rate <= -1 raise ``ValueError``.

    Returns a dict with ``rnpv``, ``unadjusted_npv``, ``pos`` (clamped), the
    ``revenue_curve``, the ``cash_flow_curve``, the echoed ``assumptions``, and a ``note``.
    """
    for label, value in (
        ("years_to_peak", years_to_peak),
        ("ramp_years", ramp_years),
        ("plateau_years", plateau_years),
        ("decline_years", decline_years),
    ):
        if int(value) < 0:
            raise ValueError(f"{label} must be non-negative, got {value}")
    if discount_rate <= -1.0:
        raise ValueError(f"discount_rate must be > -1, got {discount_rate}")

    years_to_peak = int(years_to_peak)
    ramp_years = int(ramp_years)
    plateau_years = int(plateau_years)
    decline_years = int(decline_years)

    peak = float(peak_sales_usd)
    pos_clamped = min(1.0, max(0.0, float(pos)))
    margin = float(operating_margin)
    rate = float(discount_rate)

    assumptions = {
        "peak_sales_usd": peak,
        "years_to_peak": years_to_peak,
        "discount_rate": rate,
        "ramp_years": ramp_years,
        "plateau_years": plateau_years,
        "decline_years": decline_years,
        "operating_margin": margin,
    }

    # Build the full annual revenue curve, including the leading pre-launch zeros so that
    # the curve index equals the discount year t.
    revenue_curve: List[float] = [0.0] * years_to_peak
    for i in range(1, ramp_years + 1):
        revenue_curve.append(peak * i / ramp_years)
    revenue_curve.extend([peak] * plateau_years)
    for i in range(1, decline_years + 1):
        revenue_curve.append(peak * (1.0 - i / decline_years))

    # Net cash flow, then present value at end-of-year discounting (year 1 is the first
    # year from the valuation date).
    cash_flow_curve = [rev * margin for rev in revenue_curve]
    pv_curve = [
        cf / ((1.0 + rate) ** (t + 1)) for t, cf in enumerate(cash_flow_curve)
    ]
    unadjusted_npv = sum(pv_curve)
    rnpv = unadjusted_npv * pos_clamped

    # Guardrails: a non-positive peak or a zeroed-out probability collapses rNPV to 0, and
    # we say so rather than returning a bare 0.
    if peak <= 0:
        note = (
            "peak_sales_usd <= 0, so there is no revenue to value: rNPV is 0. "
            + _METHOD_NOTE
        )
        # With a non-positive peak the curve is already all zeros or negative; force a
        # clean zero curve and zero valuation so the output is unambiguous.
        revenue_curve = [0.0] * len(revenue_curve)
        cash_flow_curve = [0.0] * len(cash_flow_curve)
        unadjusted_npv = 0.0
        rnpv = 0.0
    elif pos_clamped == 0:
        note = (
            "pos is 0, so the risk-adjusted rNPV is 0 regardless of the unadjusted NPV "
            f"of ${_billions(unadjusted_npv)}B. " + _METHOD_NOTE
        )
    else:
        note = _METHOD_NOTE

    return {
        "rnpv": rnpv,
        "unadjusted_npv": unadjusted_npv,
        "pos": pos_clamped,
        "revenue_curve": revenue_curve,
        "cash_flow_curve": cash_flow_curve,
        "assumptions": assumptions,
        "rnpv_str": f"${_billions(rnpv)}B",
        "note": note,
    }


_METHOD_NOTE = (
    "rNPV = pos * sum over years of (revenue * operating_margin) discounted at "
    "discount_rate (end-of-year). Revenue follows a linear ramp to peak, a flat plateau, "
    "then a linear decline to 0 at loss of exclusivity. The operating margin is a single "
    "blended assumption, not a grounded figure; treat the output as a transparent "
    "order-of-magnitude valuation, not a precise point estimate."
)


def _billions(value: float) -> str:
    """Render a dollar figure in billions with one decimal, e.g. 8.64e8 -> '0.9'."""
    return f"{value / 1e9:.1f}"


def rnpv_claim(
    peak_sales_usd: float,
    pos: float,
    evidence: List[Evidence] = None,
    confidence: float = 0.3,
    **kwargs: Any,
) -> Claim:
    """Wrap :func:`compute_rnpv` as a memo :class:`Claim` carrying the rNPV as its value.

    ``evidence`` and ``confidence`` come from whatever grounded the peak-sales and PoS
    inputs; a claim with no evidence is unsupported by construction (see
    :class:`Claim.is_grounded`). Extra keyword arguments pass straight through to
    :func:`compute_rnpv` (e.g. ``discount_rate``, ``operating_margin``).
    """
    result = compute_rnpv(peak_sales_usd, pos, **kwargs)
    a = result["assumptions"]
    rationale = (
        f"rNPV = pos ({result['pos']:.2f}) * unadjusted NPV "
        f"(${_billions(result['unadjusted_npv'])}B) = {result['rnpv_str']}. "
        f"Discounted at {a['discount_rate']:.0%} on a "
        f"{a['ramp_years']}y ramp / {a['plateau_years']}y plateau / "
        f"{a['decline_years']}y decline curve launching after {a['years_to_peak']}y, "
        f"at a {a['operating_margin']:.0%} operating margin. "
        "Arithmetic done deterministically in code."
    )
    return Claim(
        statement=f"Risk-adjusted NPV of the lead program: {result['rnpv_str']}",
        confidence=float(confidence),
        rationale=rationale,
        evidence=list(evidence) if evidence else [],
        value=result["rnpv_str"],
    )
