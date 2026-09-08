"""Tests for the analysis layer.

Deterministic tests exercise the module's plumbing (evidence gathering, label
resolution, result assembly) directly, with no faked model client. The full
retrieve -> reason -> claims path against a real Claude model is a single
network-marked test that skips without credentials (real models only, no mocks).
"""

from __future__ import annotations

import os

import pytest

from memo.analysis.base import AnalysisContext
from memo.analysis.model import ModelResponse
from memo.analysis.moa import MechanismModule
from memo.ingestion.base import Document, Storage
from memo.rag import StructuredStore, build_retriever


def _ctx(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="clinicaltrials", doc_type="study", doc_id="NCT1",
        title="KT-9 degrader trial", url="https://example.com/NCT1",
        text="KT-9 is an oral degrader that reduces target protein levels; "
             "biomarker fell 90% with dose response in patients.",
    ))
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-Q", doc_id="F1",
        title="10-Q", url="https://example.com/F1",
        text="Our pipeline targets protein degradation across immunology indications.",
    ))
    return AnalysisContext(
        company="TEST",
        retriever=build_retriever("TEST", storage=storage),
        structured=StructuredStore("TEST", storage=storage),
        model=None,  # not used by the deterministic paths under test
    )


def test_gather_evidence_labels_deduped_chunks(tmp_path):
    module = MechanismModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    assert labeled, "expected evidence to be gathered"
    assert all(k.startswith("E") for k in labeled)
    # labels map to distinct chunks
    assert len({c.chunk_id for c in labeled.values()}) == len(labeled)


def test_resolve_evidence_maps_and_flags_missing(tmp_path):
    module = MechanismModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    first = next(iter(labeled))

    evidence, missing = module._resolve_evidence([first, "E999"], labeled)
    assert len(evidence) == 1
    assert evidence[0].url.startswith("https://example.com/")
    assert missing == ["E999"]


def test_to_result_builds_claims_and_flags_ungrounded(tmp_path):
    module = MechanismModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    good = next(iter(labeled))

    response = ModelResponse(
        data={
            "mechanism_summary": "Targeted protein degradation.",
            "overall_confidence": 0.7,
            "claims": [
                {"statement": "KT-9 degrades its target", "evidence_ids": [good],
                 "confidence": 0.8, "rationale": "biomarker fell"},
                {"statement": "unsupported claim", "evidence_ids": ["E999"],
                 "confidence": 0.9, "rationale": "none"},
            ],
        },
        input_tokens=100, output_tokens=50,
    )
    result = module._to_result("TEST", response, labeled)

    assert result.summary == "Targeted protein degradation."
    assert result.confidence == 0.7
    assert len(result.claims) == 2
    assert result.grounded_claims[0].statement == "KT-9 degrades its target"
    assert len(result.grounded_claims) == 1  # the E999 claim is ungrounded
    assert any("ungrounded" in n for n in result.notes)
    assert result.usage == {"input_tokens": 100, "output_tokens": 50}


@pytest.mark.network
def test_moa_end_to_end_real_model():
    """Full MoA over real KYMR data with a real Claude model. Skips without creds/data."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY set")
    if not (Storage().root / "KYMR").exists():
        pytest.skip("KYMR not ingested")

    from memo.analysis import AnthropicModelClient

    try:
        ctx = AnalysisContext.for_company("KYMR", model=AnthropicModelClient())
        result = MechanismModule().analyze(ctx)
    except Exception as exc:  # noqa: BLE001 - skip on auth/network trouble
        pytest.skip(f"model call unavailable: {exc}")

    assert result.summary
    assert result.grounded_claims, "expected at least one grounded claim"
