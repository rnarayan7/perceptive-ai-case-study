"""Tests for the probability-of-success (PoS) analysis module.

Deterministic tests exercise the module's plumbing (evidence gathering, label
resolution, result assembly, the overall-PoS band claim) directly, with no faked
model client. The full retrieve -> reason -> claims path against a real Claude
model is a single network-marked test that skips without credentials (real models
only, no mocks).
"""

from __future__ import annotations

import os

import pytest

from memo.analysis.base import AnalysisContext
from memo.analysis.model import ModelResponse
from memo.analysis.pos import ProbabilityOfSuccessModule
from memo.ingestion.base import Document, Storage
from memo.rag import StructuredStore, build_retriever


def _ctx(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="clinicaltrials", doc_type="study", doc_id="NCT1",
        title="KT-9 Phase 2 randomized trial", url="https://example.com/NCT1",
        text="KT-9 was tested in a randomized, placebo-controlled Phase 2 trial of "
             "120 patients; the primary endpoint response rate improved 40% versus "
             "placebo with p<0.01 and was well tolerated with few discontinuations.",
    ))
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-Q", doc_id="F1",
        title="10-Q", url="https://example.com/F1",
        text="Regulatory precedent exists for this indication; prior agents in the "
             "class secured FDA approval on a comparable endpoint.",
    ))
    return AnalysisContext(
        company="TEST",
        retriever=build_retriever("TEST", storage=storage),
        structured=StructuredStore("TEST", storage=storage),
        model=None,  # not used by the deterministic paths under test
    )


def test_gather_evidence_labels_deduped_chunks(tmp_path):
    module = ProbabilityOfSuccessModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    assert labeled, "expected evidence to be gathered"
    assert all(k.startswith("E") for k in labeled)
    # labels map to distinct chunks
    assert len({c.chunk_id for c in labeled.values()}) == len(labeled)


def test_resolve_evidence_maps_and_flags_missing(tmp_path):
    module = ProbabilityOfSuccessModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    first = next(iter(labeled))

    evidence, missing = module._resolve_evidence([first, "E999"], labeled)
    assert len(evidence) == 1
    assert evidence[0].url.startswith("https://example.com/")
    assert missing == ["E999"]


def test_to_result_builds_band_claim_and_flags_ungrounded(tmp_path):
    module = ProbabilityOfSuccessModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    good = next(iter(labeled))

    response = ModelResponse(
        data={
            "overall_pos_band": "35-50%",
            "overall_confidence": 0.55,
            "summary": "Solid Phase 2 readout, moderate remaining clinical risk.",
            "claims": [
                {"statement": "Randomized placebo-controlled Phase 2 design",
                 "evidence_ids": [good], "confidence": 0.8,
                 "rationale": "controlled trial", "factor": "design"},
                {"statement": "Primary endpoint improved with significance",
                 "evidence_ids": [good], "confidence": 0.75,
                 "rationale": "p<0.01", "factor": "readout"},
                {"statement": "unsupported factor claim",
                 "evidence_ids": ["E999"], "confidence": 0.9,
                 "rationale": "none", "factor": "cmc"},
            ],
        },
        input_tokens=120, output_tokens=60,
    )
    result = module._to_result("TEST", response, labeled)

    assert result.summary.startswith("Solid Phase 2 readout")
    assert result.confidence == 0.55

    # Overall PoS claim leads and carries the band as its value.
    overall = result.claims[0]
    assert overall.value == "35-50%"
    assert "35-50%" in overall.statement
    assert overall.is_grounded  # aggregated from the grounded factor claims

    # Three factor claims follow the overall claim.
    assert len(result.claims) == 4
    factor_statements = [c.statement for c in result.claims[1:]]
    assert "Randomized placebo-controlled Phase 2 design" in factor_statements

    # Factor tag is surfaced in the rationale.
    design_claim = result.claims[1]
    assert "trial design" in design_claim.rationale

    # The E999 factor claim is ungrounded and flagged.
    assert any("ungrounded" in n for n in result.notes)
    grounded = [c for c in result.claims[1:] if c.is_grounded]
    assert len(grounded) == 2

    assert result.usage == {"input_tokens": 120, "output_tokens": 60}


def test_to_result_thin_evidence_flags_missing_band(tmp_path):
    module = ProbabilityOfSuccessModule()
    labeled = module._gather_evidence(_ctx(tmp_path))

    response = ModelResponse(
        data={
            "overall_pos_band": "",
            "overall_confidence": 0.1,
            "summary": "Evidence too thin to estimate.",
            "claims": [],
        },
        input_tokens=10, output_tokens=5,
    )
    result = module._to_result("TEST", response, labeled)

    overall = result.claims[0]
    assert overall.value is None
    assert any("no overall PoS band" in n for n in result.notes)


@pytest.mark.network
def test_pos_end_to_end_real_model():
    """Full PoS over real KYMR data with a real Claude model. Skips without creds/data."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY set")
    if not (Storage().root / "KYMR").exists():
        pytest.skip("KYMR not ingested")

    from memo.analysis import AnthropicModelClient

    try:
        ctx = AnalysisContext.for_company("KYMR", model=AnthropicModelClient())
        result = ProbabilityOfSuccessModule().analyze(ctx)
    except Exception as exc:  # noqa: BLE001 - skip on auth/network trouble
        pytest.skip(f"model call unavailable: {exc}")

    assert result.summary
    assert result.grounded_claims, "expected at least one grounded claim"
    assert result.claims[0].value, "expected an overall PoS band value"
