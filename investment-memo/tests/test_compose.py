"""Tests for the memo composition engine.

Fully deterministic and offline: a ``StubModelClient`` returns canned ``ModelResponse``
objects for every model call in the pipeline (each analysis module, each section draft,
and the thesis synthesis), routed by the JSON schema it is handed. A tiny synthetic
corpus in a tmp ``Storage`` gives the real retriever something to ground on, so the
whole compose_memo flow runs end to end with no network.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Set

from memo.analysis.model import ModelClient, ModelResponse
from memo.compose import compose_memo, load_artifact, render_markdown_for
from memo.ingestion.base import Document, Storage
from memo.ledger import LedgerStore

REQUIRED_SECTIONS = {"thesis", "overview", "moa", "pos", "regulatory",
                     "peak_sales", "valuation", "sources"}


class StubModelClient(ModelClient):
    """Deterministic model client. Routes on the schema's property names.

    ``ungrounded_modules`` makes the named modules cite an evidence id we never provided,
    so their claims resolve to nothing grounded -- the hook the refusal test needs.
    """

    def __init__(self, ungrounded_modules: Set[str] = frozenset()) -> None:
        self.ungrounded_modules = set(ungrounded_modules)
        self.tracer = None
        self.run_id = None
        self.schemas_seen = []

    def complete_json(self, system, user, schema, max_tokens: int = 16000) -> ModelResponse:
        props = set(schema.get("properties", {}).keys())
        self.schemas_seen.append(props)
        return ModelResponse(data=self._route(props), input_tokens=10, output_tokens=5)

    def _route(self, props: Set[str]) -> Dict[str, Any]:
        if "mechanism_summary" in props:
            return self._moa()
        if "overall_pos_band" in props:
            return self._pos()
        if "parameters" in props:
            return self._peak_sales()
        if "missing_inputs" in props:
            return self._price()
        if "recommendation" in props:
            return self._thesis()
        if "prose" in props:
            return {"prose": "Grounded prose that leans on the cited evidence [1]."}
        return self._regulatory()  # the only remaining schema: regulatory

    def _moa(self):
        eid = "E999" if "moa" in self.ungrounded_modules else "E1"
        return {
            "mechanism_summary": "An oral degrader lowers the target protein.",
            "overall_confidence": 0.7,
            "claims": [{
                "statement": "The drug degrades its target protein.",
                "evidence_ids": [eid], "confidence": 0.8,
                "rationale": "biomarker fell with dose response",
            }],
        }

    def _pos(self):
        return {
            "overall_pos_band": "35-50%", "overall_confidence": 0.5,
            "summary": "A mid-range probability of success.",
            "claims": [{
                "statement": "The randomized design supports the readout.",
                "evidence_ids": ["E1"], "confidence": 0.6,
                "rationale": "randomized controlled trial", "factor": "design",
            }],
        }

    def _regulatory(self):
        return {
            "summary": "Phase 2; no PDUFA date disclosed.", "overall_confidence": 0.5,
            "claims": [{
                "statement": "The lead program is in Phase 2.",
                "evidence_ids": ["E1"], "confidence": 0.6,
                "rationale": "trial record", "aspect": "phase",
            }],
        }

    def _peak_sales(self):
        return {
            "summary": "Thin epidemiology; net price assumed.", "overall_confidence": 0.4,
            "parameters": [
                {"name": "epidemiology_population", "value": 50000, "unit": "patients",
                 "evidence_ids": ["E1"], "rationale": "prevalence stated",
                 "confidence": 0.5, "assumed": False},
                {"name": "addressable_fraction", "value": 0.5, "unit": "fraction",
                 "evidence_ids": [], "rationale": "assumed", "confidence": 0.2,
                 "assumed": True},
                {"name": "peak_penetration", "value": 0.2, "unit": "fraction",
                 "evidence_ids": [], "rationale": "assumed", "confidence": 0.2,
                 "assumed": True},
                {"name": "annual_net_price", "value": 100000,
                 "unit": "USD/patient/year", "evidence_ids": [], "rationale": "assumed",
                 "confidence": 0.2, "assumed": True},
                {"name": "probability_of_success", "value": 0.3, "unit": "probability",
                 "evidence_ids": ["E1"], "rationale": "from readouts", "confidence": 0.4,
                 "assumed": False},
            ],
        }

    def _price(self):
        return {
            "summary": "Balance sheet extracted; no live price in corpus.",
            "overall_confidence": 0.5, "missing_inputs": ["live share price"],
            "claims": [{
                "statement": "Cash and equivalents were $300M at quarter end.",
                "evidence_ids": ["E1"], "confidence": 0.7,
                "rationale": "balance sheet", "metric": "cash",
            }],
        }

    def _thesis(self):
        return {
            "recommendation": "Constructive; size for binary readout risk.",
            "thesis": "The degrader thesis rests on the mechanism proof [1].",
            "where_view_departs": "The market underweights the mechanism evidence.",
        }


def _corpus(tmp_path) -> Storage:
    """A tiny synthetic corpus covering every module's retrieval queries."""
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="clinicaltrials", doc_type="study", doc_id="NCT1",
        title="KT-9 Phase 2 trial", url="https://example.com/NCT1",
        text=(
            "KT-9 is an oral degrader targeting a disease-driving protein; its mechanism "
            "of action is targeted protein degradation. In this randomized controlled "
            "Phase 2 trial the primary endpoint response rate improved with a large "
            "effect size and statistical significance; the biomarker fell with dose "
            "response. Safety and tolerability were acceptable with few adverse events "
            "and low discontinuation. Enrollment covered patients across the indication."
        ),
    ))
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-Q", doc_id="F1",
        title="Quarterly report", url="https://example.com/F1",
        text=(
            "Cash and cash equivalents and marketable securities were $300 million. "
            "Common shares outstanding totaled 60 million. We have convertible notes "
            "payable as long-term debt. Net loss and total operating expenses imply a "
            "cash runway funding operations into 2027. Regulatory path: fast track and "
            "orphan drug designation; a planned BLA filing with a PDUFA target; an FDA "
            "advisory committee is possible."
        ),
    ))
    storage.write_document(Document(
        company="TEST", source="pubmed", doc_type="article", doc_id="P1",
        title="Disease epidemiology", url="https://example.com/P1",
        text=(
            "Disease epidemiology: prevalence and incidence figures indicate a sizable "
            "number of patients. The addressable eligible patient population depends on "
            "line of therapy. Market size and revenue opportunity scale with peak "
            "penetration and annual net price relative to analog therapy pricing."
        ),
    ))
    return storage


def test_compose_memo_end_to_end(tmp_path):
    storage = _corpus(tmp_path)
    ledger = LedgerStore(":memory:")
    model = StubModelClient()

    memo_id = compose_memo("TEST", model, storage=storage, ledger=ledger)

    # Memo persisted and complete.
    memo = ledger.get_memo(memo_id)
    assert memo is not None
    assert memo.status == "complete"
    assert memo.recommendation  # thesis synthesis set a recommendation
    assert memo.thesis

    # Claims landed in the ledger, tagged by section and grounded.
    moa_claims = ledger.get_claims(memo_id, "moa")
    assert moa_claims, "expected moa claims in the ledger"
    assert any(c.evidence for c in moa_claims), "expected grounded moa claims"
    pos_claims = ledger.get_claims(memo_id, "pos")
    assert any(c.value and "%" in c.value for c in pos_claims)

    # Every ledger evidence id, for the citation cross-check below.
    real_evidence_ids = {
        ev.evidence_id
        for c in ledger.get_claims(memo_id)
        for ev in c.evidence
    }
    assert real_evidence_ids

    # Rendered-memo JSON written with the shape the web app consumes.
    artifact_path = tmp_path / "memos" / f"{memo_id}.json"
    assert artifact_path.exists()
    memo_json = json.loads(artifact_path.read_text())
    assert memo_json["memo_id"] == memo_id
    assert memo_json["company"] == "TEST"
    assert memo_json["title"]
    assert memo_json["recommendation"] == memo.recommendation
    assert memo_json["thesis"] == memo.thesis

    section_ids = {s["section_id"] for s in memo_json["sections"]}
    assert REQUIRED_SECTIONS.issubset(section_ids)

    # Section shape + citations reference real ledger evidence ids.
    cited_any = False
    for section in memo_json["sections"]:
        assert set(section) >= {"section_id", "title", "prose", "citations", "figures"}
        for citation in section["citations"]:
            assert set(citation) >= {"marker", "evidence_id", "url", "label"}
            assert citation["evidence_id"] in real_evidence_ids
            cited_any = True
    assert cited_any, "expected at least one citation across the memo"

    # A drafted section carries an inline citation marker from the stubbed prose.
    moa_section = next(s for s in memo_json["sections"] if s["section_id"] == "moa")
    assert "[1]" in moa_section["prose"]
    assert moa_section["citations"]

    # Figures degrade gracefully to empty (figures package not importable here).
    assert all(s["figures"] == [] for s in memo_json["sections"])


def test_valuation_integrates_when_inputs_present(tmp_path):
    """rNPV drives the valuation section when peak sales + PoS are available."""
    storage = _corpus(tmp_path)
    ledger = LedgerStore(":memory:")
    memo_id = compose_memo("TEST", StubModelClient(), storage=storage, ledger=ledger)

    memo_json = load_artifact(memo_id, storage)
    valuation = next(s for s in memo_json["sections"] if s["section_id"] == "valuation")
    assert valuation["prose"]  # drafted (rNPV instruction reached the drafter)

    # The peak-sales section produced a computed range claim we can value.
    peak_claims = ledger.get_claims(memo_id, "peak_sales")
    assert any(c.value and "$" in c.value for c in peak_claims)


def test_refused_when_required_section_has_no_grounded_claims(tmp_path):
    storage = _corpus(tmp_path)
    ledger = LedgerStore(":memory:")
    # moa cites an evidence id we never provided -> no grounded moa claim.
    model = StubModelClient(ungrounded_modules={"moa"})

    memo_id = compose_memo("TEST", model, storage=storage, ledger=ledger)

    memo = ledger.get_memo(memo_id)
    assert memo.status == "refused"
    assert "moa" in memo.notes

    # moa claims still persisted, but ungrounded (no evidence).
    moa_claims = ledger.get_claims(memo_id, "moa")
    assert moa_claims
    assert all(not c.evidence for c in moa_claims)

    # The artifact is still written; the failed section is an honest placeholder.
    memo_json = load_artifact(memo_id, storage)
    moa_section = next(s for s in memo_json["sections"] if s["section_id"] == "moa")
    assert moa_section["citations"] == []
    assert "not grounded" in moa_section["prose"].lower()


def test_render_markdown_for_reads_artifact(tmp_path):
    storage = _corpus(tmp_path)
    ledger = LedgerStore(":memory:")
    memo_id = compose_memo("TEST", StubModelClient(), storage=storage, ledger=ledger)

    markdown = render_markdown_for(memo_id, storage)
    assert markdown.startswith("# TEST Investment Memo")
    assert "## Executive Summary & Thesis" in markdown
