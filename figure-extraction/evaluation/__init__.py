"""Stage 1 evaluation harness.

Scores the figure extractor against a gold set. The numeric scoring is fully
deterministic (see :mod:`evaluation.scorers`); an LLM judge is reserved for the
two linguistic seams (fuzzy matching in :mod:`evaluation.match`, and the
interpretive figure), and is isolated behind interfaces so the deterministic
path never depends on it.

The loop: gold figure -> extractor -> matcher -> parse both sides -> scorer ->
aggregate -> report. It runs today against :class:`~evaluation.extractors.StubExtractor`,
before any real extractor exists, so the scoring itself can be validated.
"""

from evaluation.types import (
    Family,
    GoldFigure,
    KeyedTruth,
    ParsedValue,
    Prediction,
    QuantitySpec,
    ScoreResult,
    ValueKind,
)

__all__ = [
    "Family",
    "GoldFigure",
    "KeyedTruth",
    "ParsedValue",
    "Prediction",
    "QuantitySpec",
    "ScoreResult",
    "ValueKind",
]
