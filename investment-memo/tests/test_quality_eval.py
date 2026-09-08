"""Offline tests for the analytical-quality evaluator (deterministic, no live models).

The judge is stubbed with a canned ModelResponse carrying fixed dimension scores, so we
test the score->metrics mapping and aggregation without ever calling a real model.
One optional @pytest.mark.network test self-skips without ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os

import pytest

from memo.analysis.base import AnalysisResult, Claim, Evidence
from memo.analysis.model import ModelResponse
from memo.eval.quality import QualityEvaluator, dimension_metrics


class StubJudge:
    """A ModelClient stand-in that returns a preset ModelResponse, no network."""

    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self.calls = 0

    def complete_json(self, system, user, schema, max_tokens=16000):
        self.calls += 1
        self.last_user = user
        return self._response


def _analysis():
    return AnalysisResult(
        company="TEST",
        module="moa",
        summary="KT-474 degrades IRAK4.",
        claims=[
            Claim(
                statement="KT-474 reduces IRAK4 protein",
                confidence=0.8,
                rationale="PD biomarker data",
                evidence=[
                    Evidence(
                        doc_id="NCT001", source="clinicaltrials", doc_type="study",
                        url="https://example.com/NCT001", quote="IRAK4 reduced 90%",
                    )
                ],
            ),
        ],
    )


def _response(scores, comment="overall solid", input_tokens=10, output_tokens=20):
    return ModelResponse(
        data={
            "dimensions": [
                {"name": name, "score": score, "justification": f"because {name}"}
                for name, score in scores.items()
            ],
            "overall_comment": comment,
        },
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# ---------------------------------------------------------------- pure mapping


def test_dimension_metrics_mapping():
    assert dimension_metrics(1) == {"score": 1.0, "quality": 0.0}
    assert dimension_metrics(3) == {"score": 3.0, "quality": 0.5}
    assert dimension_metrics(5) == {"score": 5.0, "quality": 1.0}


def test_dimension_metrics_clamps_out_of_band_scores():
    assert dimension_metrics(0) == {"score": 1.0, "quality": 0.0}
    assert dimension_metrics(9) == {"score": 5.0, "quality": 1.0}


# ---------------------------------------------------------------- evaluator


def test_quality_eval_per_dimension_and_aggregate():
    scores = {
        "evidence_grounding": 5,
        "specificity": 4,
        "calibration": 3,
        "coverage": 2,
        "internal_consistency": 5,
    }
    judge = StubJudge(_response(scores))
    report = QualityEvaluator(_analysis(), judge).run()

    assert judge.calls == 1

    # One CaseResult per rubric dimension, carrying the raw score and its justification.
    by_dim = {c.case_id: c for c in report.case_results}
    assert set(by_dim) == set(scores)
    for name, score in scores.items():
        assert by_dim[name].metrics == dimension_metrics(score)
        assert by_dim[name].detail["justification"] == f"because {name}"

    # Overall aggregate is the mean across dimensions.
    assert report.aggregate["score"] == pytest.approx(sum(scores.values()) / 5)
    expected_quality = sum((s - 1) / 4 for s in scores.values()) / 5
    assert report.aggregate["quality"] == pytest.approx(expected_quality)


def test_quality_eval_params_flags_uncalibrated_and_carries_usage():
    judge = StubJudge(_response({d: 4 for d in [
        "evidence_grounding", "specificity", "calibration",
        "coverage", "internal_consistency",
    ]}))
    report = QualityEvaluator(_analysis(), judge).run()

    assert report.params["calibrated"] is False
    assert report.params["module"] == "moa"
    assert report.params["dimensions"] == 5
    assert report.params["overall_comment"] == "overall solid"
    assert report.params["judge_input_tokens"] == 10
    assert report.params["judge_output_tokens"] == 20


def test_quality_eval_missing_dimension_floors_to_one():
    # Judge omits a dimension; it must default to the floor rather than vanish, so a
    # partial response cannot inflate the aggregate by dropping weak dimensions.
    partial = _response({"evidence_grounding": 5, "specificity": 5})
    report = QualityEvaluator(_analysis(), StubJudge(partial)).run()

    by_dim = {c.case_id: c for c in report.case_results}
    assert len(by_dim) == 5
    assert by_dim["calibration"].metrics["score"] == 1.0
    assert by_dim["calibration"].detail["justification"] == "not scored by judge"


@pytest.mark.network
def test_quality_eval_live_judge():
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY; skipping live judge call")

    from memo.analysis.model import default_model_client

    report = QualityEvaluator(_analysis(), default_model_client()).run()
    assert len(report.case_results) == 5
    for case in report.case_results:
        assert 1.0 <= case.metrics["score"] <= 5.0
    assert 0.0 <= report.aggregate["quality"] <= 1.0
