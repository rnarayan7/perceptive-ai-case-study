"""Offline tests for the VLM extractor and the judge matcher.

Both take an injected client, so a ``FakeClient`` exercises the real parsing and
alignment logic without any network or credentials.
"""

from __future__ import annotations

from pathlib import Path

from evaluation.extractor_vlm import VlmExtractor
from evaluation.llm import extract_json
from evaluation.match import LLMJudgeMatcher
from evaluation.run import load_gold
from evaluation.types import Family, KeyedTruth, Prediction

REPO = Path(__file__).resolve().parents[2]


class FakeClient:
    """Returns canned router/extraction replies based on the prompt."""

    def complete(self, prompt, image=None, media_type="image/png", max_tokens=1024):
        if prompt.startswith("Classify"):
            return "forest"
        # Extraction: emit values for the forest keys named in the prompt.
        return (
            '[{"quantity_key": "hazard_ratio.idh_wildtype", '
            '"value": "0.51 (95% CI 0.34-0.73)", "interval_low": 0.34, '
            '"interval_high": 0.73, "confidence": 0.7, "method": "vlm"},'
            '{"quantity_key": "hazard_ratio.ecog_ps_0", '
            '"value": "0.46 (95% CI 0.29-0.71)", "interval_low": 0.29, '
            '"interval_high": 0.71, "confidence": 0.6, "method": "vlm"},'
            '{"quantity_key": "crosses_one.set", '
            '"value": "Nonmetastatic unresectable, Age over 65, BMI over 30", '
            '"confidence": 0.5, "method": "vlm"}]'
        )


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('noise [1, 2] tail') == [1, 2]


def test_vlm_extractor_parses_and_keys_predictions():
    figure = next(f for f in load_gold(REPO / "evaluation/gold/reference_figures.json")
                  if f.figure_id == "fig04_forest")
    extractor = VlmExtractor(client=FakeClient(), base_dir=REPO, samples=2)
    preds = extractor.extract(figure)
    keys = {p.quantity_key for p in preds}
    assert keys == {"hazard_ratio.idh_wildtype", "hazard_ratio.ecog_ps_0", "crosses_one.set"}

    hr = next(p for p in preds if p.quantity_key == "hazard_ratio.idh_wildtype")
    assert hr.confidence == 0.7 and hr.interval_low == 0.34
    # ratio_ci is numeric, so two samples give two reads for the agreement signal.
    assert len([v for v in hr.reads.values() if v is not None]) == 2
    assert extractor.routed["fig04_forest"] == "forest"


def test_judge_matcher_aligns_mismatched_keys():
    truths = [KeyedTruth("f", "table", "cr_12mo.sunrise1", Family.PROPORTION, "45.9% (39/83)")]
    # prediction used a different key for the same quantity
    preds = [Prediction("f", "table", "complete_response_12m_sunrise", "45.9% (39/83)")]

    class AlignClient:
        def complete(self, prompt, **kw):
            return '{"cr_12mo.sunrise1": "complete_response_12m_sunrise"}'

    result = LLMJudgeMatcher(client=AlignClient()).match(preds, truths)
    pred, truth = result.pairs[0]
    assert pred is not None and pred.quantity_key == "complete_response_12m_sunrise"
    assert result.missed == 0


def test_judge_matcher_without_client_is_exact_only():
    truths = [KeyedTruth("f", "table", "cr_12mo.sunrise1", Family.PROPORTION, "45.9% (39/83)")]
    preds = [Prediction("f", "table", "other_key", "x")]
    result = LLMJudgeMatcher(client=None).match(preds, truths)
    assert result.missed == 1  # no judge, so the mismatch stays unmatched


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
