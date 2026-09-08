"""Records and constants passed around the harness.

Pure data and enums, no logic, so every other module can import these without a
cycle. ``Family`` names the value families a scorer dispatches on; ``QuantitySpec``
is one entry in the quantity-key contract; ``Prediction`` / ``KeyedTruth`` are the
two sides a scorer compares; ``ParsedValue`` is the typed form both are parsed to;
``ScoreResult`` is one graded value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional


class Family:
    """Value families. A scorer is registered per family (see scorers.py)."""

    CONTINUOUS = "continuous"       # a real value in a stated unit (median, depth)
    LOG_SCALE = "log_scale"         # a value read on a log axis (scored in log space)
    PROPORTION = "proportion"       # k/N, denominator matters
    RATIO_CI = "ratio_ci"           # a point estimate with a confidence interval
    FOLD_MULTIPLE = "fold_multiple" # a dimensionless ratio of two reads
    CATEGORICAL_SET = "categorical_set"  # a set answer (which rows cross 1)
    INTERPRETIVE = "interpretive"   # no single numeric truth; judged, not diffed

    #: Families that yield a real-valued error and support a dual-read agreement.
    NUMERIC = frozenset({CONTINUOUS, LOG_SCALE, RATIO_CI, FOLD_MULTIPLE})
    ALL = frozenset({
        CONTINUOUS, LOG_SCALE, PROPORTION, RATIO_CI,
        FOLD_MULTIPLE, CATEGORICAL_SET, INTERPRETIVE,
    })


class ValueKind:
    """The shapes :func:`evaluation.parse.parse_value` can produce."""

    SCALAR = "scalar"
    RATIO = "ratio"          # scalar plus a CI
    PROPORTION = "proportion"  # numerator / denominator
    SET = "set"
    SENTINEL = "sentinel"    # "not reached", "not evaluable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class QuantitySpec:
    """One entry in the quantity-key contract.

    ``key`` is the canonical identifier both the extractor and the gold agree on
    (e.g. ``"median_pfs.ozekibart"``). ``tolerance`` is the family-appropriate
    within-tolerance threshold: absolute for continuous, fold for log-scale,
    relative for fold-multiple, ignored for exact families.
    """

    key: str
    family: str
    unit: Optional[str] = None
    tolerance: Optional[float] = None
    description: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedValue:
    """A value string parsed into a comparable typed form."""

    kind: str
    raw: str = ""
    scalar: Optional[float] = None
    unit: Optional[str] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    items: Optional[FrozenSet[str]] = None
    sentinel: Optional[str] = None


@dataclass
class Prediction:
    """One value the extractor returned for a figure.

    ``interval_low``/``interval_high`` are the reported confidence interval on the
    value (used for coverage/calibration). ``reads`` holds the independent reads
    (e.g. ``{"vlm": 5.4, "cv": 5.6}``); their agreement is the keystone signal.
    """

    figure_id: str
    figure_type: str
    quantity_key: str
    value_raw: str
    unit: Optional[str] = None
    interval_low: Optional[float] = None
    interval_high: Optional[float] = None
    confidence: Optional[float] = None
    method: str = ""
    reads: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class KeyedTruth:
    """One gold value, tagged with its canonical key and family.

    This is the curated form of a corpus ``GroundTruthValue``: the free-text or
    structured value tagged with a ``quantity_key`` so an exact-key matcher can
    align it. ``verified`` gates whether it may score anything.
    """

    figure_id: str
    figure_type: str
    quantity_key: str
    family: str
    value_raw: str
    unit: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    verified: bool = True


@dataclass
class GoldFigure:
    """A figure plus its keyed gold values (the answer key for one image)."""

    figure_id: str
    figure_type: str
    image_path: Optional[str] = None
    truths: List[KeyedTruth] = field(default_factory=list)


@dataclass
class ScoreResult:
    """One graded value: what the score was and why.

    ``error``/``within_tolerance``/``interval_covers_truth`` are ``None`` for
    interpretive values (nothing to diff). ``error_category`` classifies a miss so
    the aggregate can report *kinds* of failure, not just magnitude. ``agreement``
    is the dual-read spread when available.
    """

    figure_id: str
    figure_type: str
    quantity_key: str
    family: str
    predicted_raw: str
    truth_raw: str
    error: Optional[float] = None
    rel_error: Optional[float] = None
    within_tolerance: Optional[bool] = None
    interval_covers_truth: Optional[bool] = None
    error_category: str = "ok"
    confidence: Optional[float] = None
    agreement: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)
