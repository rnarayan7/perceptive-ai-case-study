"""Offline tests for the judge-validation logic (no model, no network)."""

from __future__ import annotations

from memo.analysis.base import AnalysisResult, Claim, Evidence
from memo.eval.judge_validation import (
    corrupt_analysis,
    load_probes,
    sample_calibration,
    _cohen_kappa,
)


def _claim(stmt, quote):
    return Claim(statement=stmt, confidence=0.8, rationale="",
                 evidence=[Evidence(doc_id=stmt, source="s", doc_type="t", url="u", quote=quote)])


def test_load_probes_reads_committed_set():
    probes = load_probes("evals/judge_probes/faithfulness.json")
    assert len(probes) >= 12
    assert all(p.expected in {"supported", "partial", "unsupported", "contradicted"} for p in probes)
    assert all(p.claim and p.evidence for p in probes)


def test_corrupt_rotates_evidence_off_its_claim():
    analysis = AnalysisResult("T", "moa", "", [
        _claim("claim A", "evidence for A"),
        _claim("claim B", "evidence for B"),
        _claim("claim C", "evidence for C"),
    ])
    corrupt = corrupt_analysis(analysis)
    assert len(corrupt.claims) == 3
    # Every claim now carries a DIFFERENT claim's evidence.
    for original, moved in zip(analysis.claims, corrupt.claims):
        assert moved.statement == original.statement
        assert moved.evidence[0].quote != original.evidence[0].quote


def test_corrupt_needs_two_grounded_claims():
    one = AnalysisResult("T", "moa", "", [_claim("only", "e")])
    assert corrupt_analysis(one).claims == []


def test_sample_calibration_is_deterministic_and_blank():
    pool = [{"claim_id": f"c{i}", "company": "KYMR", "module": "moa",
             "statement": f"s{i}", "evidence": [f"e{i}"]} for i in range(10)]
    a = sample_calibration(pool, 4, seed=1)
    b = sample_calibration(pool, 4, seed=1)
    assert [x["claim_id"] for x in a] == [x["claim_id"] for x in b]  # deterministic
    assert len(a) == 4
    assert all(x["human_verdict"] == "" for x in a)  # blank for labeling
    # claims with no evidence are excluded
    assert sample_calibration([{"claim_id": "x", "company": "K", "module": "m",
                                "statement": "s", "evidence": []}], 4) == []


def test_cohen_kappa_perfect_and_chance():
    assert _cohen_kappa([(True, True), (False, False)]) == 1.0
    assert _cohen_kappa([]) is None
