"""Pulling representative numbers back out of module claim values.

The valuation step needs a single peak-sales figure and a single probability from
analysis output whose native form is a range ("$0.4-0.9B") or a band ("35-50%").
These parsers take the midpoint of such a range and are deliberately forgiving: any
value they cannot read returns ``None`` so the caller records a gap rather than
inventing a number.
"""

from __future__ import annotations

import re
from typing import List, Optional

from memo.analysis.base import AnalysisResult

# No leading sign: these values are positive magnitudes written as ranges ("0.4-0.9",
# "35-50"), where the hyphen is a range separator, not a minus. Allowing "-?" here made
# "0.4-0.9" parse as [0.4, -0.9] and invert the midpoint.
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> List[float]:
    return [float(m) for m in _NUMBER.findall(text or "")]


def _midpoint(nums: List[float]) -> Optional[float]:
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    return (min(nums) + max(nums)) / 2.0


def peak_sales_usd(result: Optional[AnalysisResult]) -> Optional[float]:
    """A single peak-sales dollar figure (midpoint of the range) from a peak_sales result.

    Looks at each claim's ``value``; the computed claim carries a range like
    ``"$0.4-0.9B"``. Returns the midpoint in absolute USD, scaling B/M/K suffixes.
    """
    if result is None:
        return None
    for claim in result.claims:
        value = claim.value or ""
        if "$" not in value:
            continue
        # Require a magnitude suffix (B/M/K). The headline peak-sales claim is always a
        # magnitude range ("$0.4-0.9B"); this skips per-unit price claims like
        # "$41.2 per dosage unit", which have no suffix and are not peak sales.
        upper = value.upper()
        if "B" in upper:
            scale = 1e9
        elif "M" in upper:
            scale = 1e6
        elif "K" in upper:
            scale = 1e3
        else:
            continue
        mid = _midpoint(_numbers(value))
        if mid is None:
            continue
        return mid * scale
    return None


def pos_fraction(result: Optional[AnalysisResult]) -> Optional[float]:
    """A single probability in [0, 1] from a pos result's overall band ("35-50%").

    Percent bands become a midpoint fraction; a bare fraction (<= 1) passes through.
    """
    if result is None:
        return None
    for claim in result.claims:
        value = claim.value or ""
        if not value:
            continue
        nums = _numbers(value)
        mid = _midpoint(nums)
        if mid is None:
            continue
        if "%" in value or mid > 1.0:
            return max(0.0, min(1.0, mid / 100.0))
        return max(0.0, min(1.0, mid))
    return None
