"""Tests for the price-vs-thesis module.

Deterministic tests exercise the pure arithmetic helper, evidence gathering over a
real retriever, and result assembly from a canned ModelResponse (no faked model
client). The full retrieve -> reason -> claims path against a real Claude model is a
single network-marked test that skips without credentials (real models only, no mocks).
"""

from __future__ import annotations

import os

import pytest

from memo.analysis.base import AnalysisContext
from memo.analysis.model import ModelResponse
from memo.analysis.price import PriceVsThesisModule, net_cash
from memo.ingestion.base import Document, Storage
from memo.rag import StructuredStore, build_retriever


def _ctx(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-Q", doc_id="F1",
        title="10-Q", url="https://example.com/F1",
        text="As of the balance sheet date the company held cash and cash equivalents "
             "and marketable securities of $420.0 million, and had 60.0 million shares "
             "of common stock outstanding.",
    ))
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-Q", doc_id="F2",
        title="10-Q", url="https://example.com/F2",
        text="The company had $50.0 million of convertible notes payable outstanding. "
             "Management believes existing cash funds operations into 2027, giving a "
             "cash runway of roughly two years.",
    ))
    return AnalysisContext(
        company="TEST",
        retriever=build_retriever("TEST", storage=storage),
        structured=StructuredStore("TEST", storage=storage),
        model=None,  # not used by the deterministic paths under test
    )


def test_net_cash_exact_values():
    assert net_cash(420.0, 50.0) == 370.0
    assert net_cash(100.0, 0.0) == 100.0
    # more debt than cash -> negative net cash
    assert net_cash(30.0, 50.0) == -20.0
    # accepts int-like inputs and returns a float
    result = net_cash(420, 50)
    assert result == 370.0
    assert isinstance(result, float)


def test_gather_evidence_labels_deduped_chunks(tmp_path):
    from memo.analysis import grounding

    labeled = grounding.gather_labeled_evidence(
        _ctx(tmp_path), ["cash and cash equivalents"]
    )
    assert labeled, "expected evidence to be gathered"
    assert all(k.startswith("E") for k in labeled)
    assert len({c.chunk_id for c in labeled.values()}) == len(labeled)


def test_to_result_builds_claims_carries_evidence_and_missing_inputs(tmp_path):
    module = PriceVsThesisModule()
    from memo.analysis import grounding

    labeled = grounding.gather_labeled_evidence(
        _ctx(tmp_path), ["cash and cash equivalents shares outstanding debt runway"]
    )
    good = next(iter(labeled))

    response = ModelResponse(
        data={
            "summary": "Given cash and debt, EV = market cap - cash + debt once a "
                       "market price is supplied.",
            "overall_confidence": 0.6,
            "missing_inputs": [
                "live share price / market cap not in corpus",
                "consensus estimates not in corpus",
            ],
            "claims": [
                {"statement": "Cash and equivalents were $420.0 million",
                 "evidence_ids": [good], "confidence": 0.9,
                 "rationale": "stated in the filing", "metric": "cash"},
                {"statement": "unsupported claim", "evidence_ids": ["E999"],
                 "confidence": 0.8, "rationale": "none", "metric": "debt"},
            ],
        },
        input_tokens=120, output_tokens=40,
    )
    result = module._to_result("TEST", response, labeled)

    assert result.summary.startswith("Given cash and debt")
    assert result.confidence == 0.6
    assert len(result.claims) == 2

    # the grounded claim carries real evidence back to a source
    grounded = result.grounded_claims
    assert len(grounded) == 1
    assert grounded[0].statement == "Cash and equivalents were $420.0 million"
    assert grounded[0].evidence[0].url.startswith("https://example.com/")
    # metric is folded into the rationale
    assert grounded[0].rationale.startswith("[cash]")

    # ungrounded claim flagged; unknown id noted
    assert any("ungrounded" in n for n in result.notes)
    assert any("unknown evidence ids" in n for n in result.notes)

    # the model's declared missing inputs land in notes...
    assert any("live share price / market cap not in corpus" in n for n in result.notes)
    assert any("consensus estimates not in corpus" in n for n in result.notes)
    # ...and the structural corpus gaps (price, consensus) are always appended
    assert any("no public source in corpus" in n for n in result.notes)
    assert sum("consensus" in n.lower() for n in result.notes) >= 1

    assert result.usage == {"input_tokens": 120, "output_tokens": 40}


def test_no_evidence_still_reports_corpus_gaps(tmp_path):
    """With an empty corpus the module makes no claims but still names its blind spots."""
    module = PriceVsThesisModule()
    storage = Storage(root=tmp_path)
    empty_ctx = AnalysisContext(
        company="EMPTY",
        retriever=build_retriever("EMPTY", storage=storage),
        structured=StructuredStore("EMPTY", storage=storage),
        model=None,
    )
    result = module.analyze(empty_ctx)
    assert result.claims == []
    assert result.confidence == 0.0
    assert any("no public source in corpus" in n for n in result.notes)


@pytest.mark.network
def test_price_end_to_end_real_model():
    """Full price read over real KYMR data with a real Claude model. Skips without creds/data."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY set")
    if not (Storage().root / "KYMR").exists():
        pytest.skip("KYMR not ingested")

    from memo.analysis import AnthropicModelClient

    try:
        ctx = AnalysisContext.for_company("KYMR", model=AnthropicModelClient())
        result = PriceVsThesisModule().analyze(ctx)
    except Exception as exc:  # noqa: BLE001 - skip on auth/network trouble
        pytest.skip(f"model call unavailable: {exc}")

    assert result.summary
    # price and consensus must never be silently assumed available
    assert any("no public source in corpus" in n for n in result.notes)
