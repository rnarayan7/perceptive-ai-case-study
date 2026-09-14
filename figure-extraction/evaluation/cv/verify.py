"""The verification layer: confidence as the conjunction of invariant checks.

A measurement is only as trustworthy as the weakest stage that produced it. The
earlier build checked the *axis* and nothing else, which is how a panel read
54 bars as 48 and still reported high confidence: the axis was perfect, the
segmentation was not, and nothing tested the segmentation.

So every stage declares invariants, each invariant is a pass/fail check, and
confidence is the conjunction. The checks are all *internal* to the figure (does
the axis reproduce a held-out reference line, does the bar count match the N the
figure prints, is the panel actually sorted), so they need no ground truth and no
prior exposure to the figure's style. That is what makes them usable on an image
the system has never seen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Check:
    """One invariant test and its outcome."""

    name: str
    passed: bool
    detail: str = ""
    critical: bool = True     # a failed critical check forces a decline

    def __str__(self) -> str:
        mark = "pass" if self.passed else "FAIL"
        return f"[{mark}] {self.name}{(': ' + self.detail) if self.detail else ''}"


@dataclass
class Verdict:
    """The result of verifying one panel's measurement."""

    checks: List[Check] = field(default_factory=list)

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def critical_failures(self) -> List[Check]:
        return [c for c in self.failed if c.critical]

    @property
    def accepted(self) -> bool:
        """Accept only when no critical invariant failed."""
        return not self.critical_failures

    @property
    def confidence(self) -> str:
        if not self.accepted:
            return "DECLINE"
        if self.failed:
            return "MEDIUM"
        return "HIGH"

    def ledger(self) -> str:
        return "; ".join(str(c) for c in self.checks)


# --- the invariant checks --------------------------------------------------

def check_axis(auto_axis, tol_note: str = "") -> Check:
    """The axis mapping must reproduce a reference line it was not built from."""
    if auto_axis is None:
        return Check("axis_calibrated", False, "no axis could be built")
    if auto_axis.check_error is None:
        # Only two reference lines: the mapping is exactly determined, so there
        # is nothing held out to test it against. Usable, but unverified.
        return Check("axis_round_trip", True,
                     "only 2 reference lines, mapping unverified", critical=False)
    return Check("axis_round_trip", bool(auto_axis.ok),
                 f"held-out line off by {auto_axis.check_error:.2f}{tol_note}")


def check_count(measured: int, stated: Optional[int], slack: int = 2) -> Check:
    """The bar count must match the N the figure prints about itself.

    This is the check that catches a merged or over-split panel even when the
    axis is flawless, and it is the one whose absence produced a confident wrong
    answer in the previous build.
    """
    if stated is None:
        return Check("count_vs_stated_n", True, "no N printed; unverified",
                     critical=False)
    ok = abs(measured - stated) <= slack
    return Check("count_vs_stated_n", ok, f"measured {measured} vs stated {stated}")


def check_sorted(values, slack: float = 1.0) -> Check:
    """A waterfall panel must be sorted; if it is not, the split is wrong."""
    from evaluation.cv.primitives import is_descending
    ok = is_descending(values, slack=slack)
    return Check("panel_sorted", ok,
                 "values descend" if ok else "values rise inside a panel")


def check_resolution(bar_px: float, min_px: float = 3.0) -> Check:
    """Bars narrower than a few pixels cannot be counted reliably."""
    ok = bar_px >= min_px
    return Check("bar_resolution", ok, f"{bar_px:.1f}px per bar", critical=False)


def check_agreement(cv_value: Optional[float], vlm_value: Optional[float],
                    tol: float) -> Check:
    """The two independent reads should agree; divergence is the triage signal."""
    if cv_value is None or vlm_value is None:
        return Check("cv_vlm_agreement", True, "only one read available",
                     critical=False)
    ok = abs(cv_value - vlm_value) <= tol
    return Check("cv_vlm_agreement", ok,
                 f"cv {cv_value:.1f} vs vlm {vlm_value:.1f}", critical=False)


def check_grid_fill(fill: float, min_fill: float = 0.95) -> Check:
    """Every slot of an N-wide grid should contain a mark.

    This replaces the count check when N is used to *drive* segmentation rather
    than to verify it. A grid laid over the wrong span, or over a panel whose
    real bar count differs from the stated N, leaves empty slots; a grid that
    fits the figure fills all of them.
    """
    ok = fill >= min_fill
    return Check("grid_fill", ok, f"{fill*100:.0f}% of slots filled")
