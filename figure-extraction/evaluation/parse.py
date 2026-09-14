"""Parse a value string into a comparable typed :class:`ParsedValue`.

Deterministic and shared by both sides: a prediction and its gold go through the
same parser so they are compared like-for-like. Handles the shapes that appear
beside oncology figures: sentinels ("not reached"), ratios with a CI, proportions
with a denominator, plain scalars with a unit, and comma-separated set answers.

The expected ``family`` steers ambiguous cases (a comma-separated string is a set
only when the family says so; ``"45.9% (39/83)"`` is a proportion, not a scalar).
"""

from __future__ import annotations

import re
from typing import FrozenSet, Optional

from evaluation.types import Family, ParsedValue, ValueKind

_SENTINELS = {
    "not reached": "not_reached",
    "nr": "not_reached",
    "not evaluable": "not_evaluable",
    "ne": "not_evaluable",
    "not reported": "not_reported",
    "not estimable": "not_reached",
}

# "0.48 (95% CI 0.33-0.72)" / "0.5 (95% CI 0.33 to 0.72)"
_RATIO_CI = re.compile(
    r"(?P<pt>-?\d+(?:\.\d+)?)\s*\(?\s*95%?\s*CI[:\s]*"
    r"(?P<lo>-?\d+(?:\.\d+)?)\s*(?:[-–—]|to|,)\s*(?P<hi>-?\d+(?:\.\d+)?)",
    re.I,
)
# "46.4% (51/110)" or "51/110"
_PROPORTION = re.compile(
    r"(?:(?P<pct>-?\d+(?:\.\d+)?)\s*%\s*)?\(?\s*(?P<num>\d+)\s*/\s*(?P<den>\d+)\s*\)?"
)
# leading number with optional unit word: "5.5 months", "0.25 nM", "+62%", "-95"
_SCALAR = re.compile(
    r"^[~≈+]?\s*(?P<val>-?\d+(?:\.\d+)?)\s*(?P<unit>%|[a-zA-Zµ/]+)?\b"
)


def parse_value(raw: str, unit: Optional[str] = None, family: Optional[str] = None) -> ParsedValue:
    """Parse ``raw`` into a :class:`ParsedValue`, guided by the expected family."""
    text = (raw or "").strip()
    if not text:
        return ParsedValue(kind=ValueKind.UNKNOWN, raw=raw, unit=unit)

    sentinel = _match_sentinel(text)
    if sentinel is not None:
        return ParsedValue(kind=ValueKind.SENTINEL, raw=raw, sentinel=sentinel, unit=unit)

    if family == Family.CATEGORICAL_SET:
        return ParsedValue(kind=ValueKind.SET, raw=raw, items=_parse_set(text))

    if family == Family.RATIO_CI or _RATIO_CI.search(text):
        m = _RATIO_CI.search(text)
        if m:
            return ParsedValue(
                kind=ValueKind.RATIO, raw=raw, unit=unit,
                scalar=float(m.group("pt")),
                ci_low=float(m.group("lo")), ci_high=float(m.group("hi")),
            )

    if family == Family.PROPORTION:
        m = _PROPORTION.search(text)
        if m and m.group("num") and m.group("den"):
            return ParsedValue(
                kind=ValueKind.PROPORTION, raw=raw,
                numerator=int(m.group("num")), denominator=int(m.group("den")),
                scalar=float(m.group("pct")) if m.group("pct") else None, unit="%",
            )
        # A bare percentage with no denominator still parses as a scalar percent.

    m = _SCALAR.match(text)
    if m:
        parsed_unit = m.group("unit") or (unit if unit not in ("probability", "count") else None)
        value = float(m.group("val"))
        # A quantity whose expected unit is a probability may be answered either
        # as 0.47 or as "47%". Both are correct readings of the same landmark, so
        # the percent form is converted rather than scored against a decimal:
        # comparing 47 with 0.47 otherwise reports an error of 46 for an answer
        # that was right. Only an explicit percent sign triggers this, so a
        # genuine decimal is left alone.
        if unit == "probability" and "%" in text and value > 1.0:
            value /= 100.0
            parsed_unit = "probability"
        return ParsedValue(
            kind=ValueKind.SCALAR, raw=raw,
            scalar=value, unit=parsed_unit,
        )

    return ParsedValue(kind=ValueKind.UNKNOWN, raw=raw, unit=unit)


def _match_sentinel(text: str) -> Optional[str]:
    low = text.lower().strip(" .")
    return _SENTINELS.get(low)


def _parse_set(text: str) -> FrozenSet[str]:
    """Split a set answer on commas/semicolons and normalize each item."""
    parts = re.split(r"[;,]", text)
    return frozenset(_normalize_item(p) for p in parts if p.strip())


def _normalize_item(item: str) -> str:
    """Lowercase, collapse whitespace, drop punctuation for set comparison."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", item.lower()).strip()
    return re.sub(r"\s+", " ", cleaned)
