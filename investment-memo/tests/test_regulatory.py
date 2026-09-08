"""Tests for the regulatory-path analysis module.

Deterministic tests exercise the module's plumbing (evidence gathering, label
resolution, result assembly, and the honest "not disclosed" claim) directly, with no
faked model client. The full retrieve -> reason -> claims path against a real Claude
model is a single network-marked test that skips without credentials (real models only,
no mocks).
"""

from __future__ import annotations

import os

import pytest

from memo.analysis.base import AnalysisContext
from memo.analysis.model import ModelResponse
from memo.analysis.regulatory import RegulatoryModule
from memo.ingestion.base import Document, Storage
from memo.rag import StructuredStore, build_retriever


def _ctx(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="clinicaltrials", doc_type="study", doc_id="NCT1",
        title="KT-9 registrational Phase 3", url="https://example.com/NCT1",
        text="KT-9 is in a registrational Phase 3 pivotal trial. The FDA granted "
             "breakthrough therapy designation and orphan drug designation. The primary "
             "endpoint is a surrogate that may support accelerated approval.",
    ))
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-Q", doc_id="F1",
        title="10-Q", url="https://example.com/F1",
        text="We plan to submit an NDA filing to the FDA and anticipate an advisory "
             "committee meeting may be convened to review the application.",
    ))
    return AnalysisContext(
        company="TEST",
        retriever=build_retriever("TEST", storage=storage),
        structured=StructuredStore("TEST", storage=storage),
        model=None,  # not used by the deterministic paths under test
    )


def test_gather_evidence_labels_deduped_chunks(tmp_path):
    module = RegulatoryModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    assert labeled, "expected evidence to be gathered"
    assert all(k.startswith("E") for k in labeled)
    # labels map to distinct chunks
    assert len({c.chunk_id for c in labeled.values()}) == len(labeled)


def test_resolve_evidence_maps_and_flags_missing(tmp_path):
    module = RegulatoryModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    first = next(iter(labeled))

    evidence, missing = module._resolve_evidence([first, "E999"], labeled)
    assert len(evidence) == 1
    assert evidence[0].url.startswith("https://example.com/")
    assert missing == ["E999"]


def test_to_result_builds_claims_and_flags_ungrounded(tmp_path):
    module = RegulatoryModule()
    labeled = module._gather_evidence(_ctx(tmp_path))
    good = next(iter(labeled))

    response = ModelResponse(
        data={
            "summary": "Registrational Phase 3 with breakthrough and orphan designations.",
            "overall_confidence": 0.6,
            "claims": [
                {"statement": "KT-9 has FDA breakthrough therapy designation",
                 "evidence_ids": [good], "confidence": 0.8,
                 "rationale": "stated in trial record", "aspect": "designation"},
                {"statement": "fabricated PDUFA date of March 2026",
                 "evidence_ids": ["E999"], "confidence": 0.9,
                 "rationale": "none", "aspect": "filing"},
            ],
        },
        input_tokens=120, output_tokens=60,
    )
    result = module._to_result("TEST", response, labeled)

    assert result.summary.startswith("Registrational Phase 3")
    assert result.confidence == 0.6
    assert len(result.claims) == 2
    assert result.grounded_claims[0].statement == "KT-9 has FDA breakthrough therapy designation"
    assert len(result.grounded_claims) == 1  # the E999 claim is ungrounded
    assert any("ungrounded" in n for n in result.notes)
    # aspect is carried into the rationale for downstream display
    assert "[designation]" in result.grounded_claims[0].rationale
    assert result.usage == {"input_tokens": 120, "output_tokens": 60}


def test_not_disclosed_claim_is_valid_without_evidence(tmp_path):
    """A 'not disclosed' finding with no evidence is recorded as a claim + a note."""
    module = RegulatoryModule()
    labeled = module._gather_evidence(_ctx(tmp_path))

    response = ModelResponse(
        data={
            "summary": "Filing timeline is not disclosed.",
            "overall_confidence": 0.4,
            "claims": [
                {"statement": "No PDUFA date is disclosed in the available evidence",
                 "evidence_ids": [], "confidence": 0.5,
                 "rationale": "no source states a PDUFA date", "aspect": "filing"},
            ],
        },
        input_tokens=10, output_tokens=5,
    )
    result = module._to_result("TEST", response, labeled)

    assert len(result.claims) == 1
    # the not-disclosed claim is preserved even though it carries no evidence
    assert "disclosed" in result.claims[0].statement.lower()
    assert result.claims[0].evidence == []
    assert not result.claims[0].is_grounded
    # its absence of evidence is surfaced as a note, not silently dropped
    assert any("ungrounded" in n for n in result.notes)


def test_empty_evidence_returns_graceful_result(tmp_path):
    """With no ingested docs, analyze short-circuits without calling a model."""
    storage = Storage(root=tmp_path)
    ctx = AnalysisContext(
        company="EMPTY",
        retriever=build_retriever("EMPTY", storage=storage),
        structured=StructuredStore("EMPTY", storage=storage),
        model=None,
    )
    result = RegulatoryModule().analyze(ctx)
    assert result.claims == []
    assert result.confidence == 0.0
    assert any("no evidence" in n for n in result.notes)


@pytest.mark.network
def test_regulatory_end_to_end_real_model():
    """Full regulatory path over real KYMR data with a real Claude model.

    Skips without credentials/data, and self-skips on any exception (cost control).
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY set")
    if not (Storage().root / "KYMR").exists():
        pytest.skip("KYMR not ingested")

    from memo.analysis import AnthropicModelClient

    try:
        ctx = AnalysisContext.for_company("KYMR", model=AnthropicModelClient())
        result = RegulatoryModule().analyze(ctx)
    except Exception as exc:  # noqa: BLE001 - skip on auth/network trouble
        pytest.skip(f"model call unavailable: {exc}")

    assert result.summary
    assert result.grounded_claims, "expected at least one grounded claim"
