"""Unit tests for the corpus primitives (no network).

Covers the figure-type classifier, the candidate-value heuristic, and a
Storage round-trip, exercising the dependency-injection seams the base was
built around. Run: ``python -m corpus.tests.test_base`` or ``python -m pytest``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from corpus.base import (
    FigureRecord,
    FigureStorage,
    FigureType,
    GroundTruthValue,
    extract_candidate_values,
)


def test_classify_covers_each_type():
    assert FigureType.classify("Progression-free survival, Kaplan-Meier") == FigureType.KAPLAN_MEIER
    assert FigureType.classify("Waterfall of best percentage change") == FigureType.WATERFALL
    assert FigureType.classify("Forest plot of subgroups, favors drug") == FigureType.FOREST
    assert FigureType.classify("Plasma concentration over time (nM)") == FigureType.PK_LOGSCALE
    assert FigureType.classify("Spider plot: change in target lesion") == FigureType.SPIDER
    assert FigureType.classify("A pretty picture of a cat") == FigureType.UNKNOWN


def test_candidate_values_recover_shapes_and_denominator():
    text = "median PFS 5.5 months. HR 0.48 (95% CI 0.33-0.72). PSA50 62% (48/77)."
    gts = extract_candidate_values(text)
    kinds = {g.quantity.split()[0] for g in gts}
    assert {"hazard", "median", "proportion"} <= kinds
    prop = next(g for g in gts if g.quantity.startswith("proportion"))
    assert prop.population_n == 77 and prop.verified is False


def test_candidate_values_reject_bad_proportion():
    # numerator > denominator is not a real proportion
    assert not any(g.quantity.startswith("proportion")
                   for g in extract_candidate_values("nonsense 90/12"))


def test_storage_roundtrip_and_change_detection():
    with tempfile.TemporaryDirectory() as tmp:
        storage = FigureStorage(root=Path(tmp))
        record = FigureRecord(
            source="test", figure_id="fig1", figure_type=FigureType.FOREST,
            title="t", image_ext="png", image_bytes=b"\x89PNG\r\n\x1a\nDATA",
            ground_truth=[GroundTruthValue(quantity="hr", value="0.5", verified=True)],
        )
        assert storage.is_unchanged(record) is False
        storage.write_figure(record)
        assert storage.is_unchanged(record) is True  # same bytes now on disk

        loaded = storage.load_figures(source="test", figure_type=FigureType.FOREST)
        assert len(loaded) == 1
        assert loaded[0].figure_id == "fig1"
        assert loaded[0].ground_truth[0].verified is True

        # a filter that excludes the type returns nothing
        assert storage.load_figures(figure_type=FigureType.WATERFALL) == []


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
