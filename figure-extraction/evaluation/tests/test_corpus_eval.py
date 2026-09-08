"""Tests for the labeled-corpus evaluation pipeline (no network).

A fake extractor and a fake judge client exercise the real alignment and scoring
logic: a prediction whose label differs from the gold's label is aligned by the
judge and then scored deterministically.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from corpus.base import FigureRecord, FigureStorage, FigureType, GroundTruthValue
from evaluation.corpus_eval import (
    HarvestedExtractor,
    LabeledFigure,
    evaluate_corpus,
    infer_family,
    infer_tolerance,
    load_labeled,
)
from evaluation.match import LLMJudgeMatcher
from evaluation.types import Family, KeyedTruth, Prediction


def test_infer_family():
    assert infer_family("0.50 (95% CI 0.33-0.72)") == Family.RATIO_CI
    assert infer_family("46% (51/110)") == Family.PROPORTION
    assert infer_family("5.5 months") == Family.CONTINUOUS


def test_infer_tolerance_relative_for_continuous():
    assert infer_tolerance(Family.PROPORTION, "46% (51/110)") is None      # exact
    assert infer_tolerance(Family.RATIO_CI, "0.5") == 0.1
    assert abs(infer_tolerance(Family.CONTINUOUS, "5.5 months") - 0.55) < 1e-9  # 10%


def test_evaluate_corpus_judge_aligns_and_scores():
    truth = KeyedTruth("f", "forest", "HR IDH wildtype", Family.RATIO_CI,
                       "0.50 (95% CI 0.33-0.72)")
    fig = LabeledFigure("f", "forest", "pmc", Path("/none.png"), [truth])
    # prediction uses a different label for the same quantity, close value
    pred = Prediction("f", "forest", "hazard ratio for IDH-wt", "0.51 (95% CI 0.34-0.73)",
                      confidence=0.7)

    class FakeExtractor:
        def extract(self, figure):
            return [pred]

    class JudgeClient:
        def complete(self, prompt, **kw):
            return '{"HR IDH wildtype": "hazard ratio for IDH-wt"}'

    results, missed = evaluate_corpus([fig], FakeExtractor(), LLMJudgeMatcher(JudgeClient()))
    assert missed == 0 and len(results) == 1
    assert results[0].within_tolerance is True   # point error 0.01 <= 0.1


def test_evaluate_corpus_counts_miss_when_unaligned():
    truth = KeyedTruth("f", "forest", "HR", Family.RATIO_CI, "0.50 (95% CI 0.33-0.72)")
    fig = LabeledFigure("f", "forest", "pmc", Path("/none.png"), [truth])

    class EmptyExtractor:
        def extract(self, figure):
            return []

    results, missed = evaluate_corpus([fig], EmptyExtractor(), LLMJudgeMatcher(None))
    assert missed == 1 and results[0].error_category == "missed"


def test_load_labeled_reads_verified_only():
    with tempfile.TemporaryDirectory() as d:
        storage = FigureStorage(root=Path(d))
        storage.write_figure(FigureRecord(
            source="pmc", figure_id="PMC1_f1", figure_type=FigureType.FOREST,
            image_ext="png", image_bytes=b"\x89PNG\r\n\x1a\nX",
            ground_truth=[
                GroundTruthValue(quantity="HR", value="0.5", method="manual", verified=True),
                GroundTruthValue(quantity="cand", value="0.9", method="regex_candidate"),
            ]))
        labeled = load_labeled(storage)
        assert len(labeled) == 1
        assert [t.value_raw for t in labeled[0].truths] == ["0.5"]  # only the verified one


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
