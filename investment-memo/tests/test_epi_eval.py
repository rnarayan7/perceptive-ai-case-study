"""Offline tests for the epidemiology-grounding evals and sanity check.

Deterministic, no network, no models. The epi-grounding evaluator inspects a hand-built
AnalysisResult (in production it is generated with a real model, never a mock).
"""

from __future__ import annotations

import pytest

from memo.analysis.base import AnalysisResult, Claim, Evidence
from memo.analysis.peak_sales import (
    POP_PLAUSIBILITY_CEILING,
    POP_PLAUSIBILITY_FLOOR,
    epidemiology_population_flags,
)
from memo.eval.epi import (
    EpiGroundingEvaluator,
    EpiReference,
    EpiRetrievalEvaluator,
    load_epi_reference,
)
from memo.ingestion.base import Document, Storage
from memo.rag import build_retriever


def _ev(source):
    return Evidence(doc_id="d1", source=source, doc_type="epi", url="u", quote="q")


def _reference(company="TEST"):
    return EpiReference(
        company=company,
        lead_indication="focal epilepsy",
        indication_terms=["focal", "epilepsy", "seizure"],
        epi_terms=["prevalence", "incidence", "patients", "population", "million"],
        plausible_population_min=100_000,
        plausible_population_max=5_000_000,
    )


# ----------------------------------------------------------- module sanity check


def test_flags_grounded_and_plausible_population():
    cited, plausible, notes = epidemiology_population_flags(500_000, [_ev("pubmed")])
    assert cited and plausible
    assert any("grounded on epidemiology source" in n for n in notes)


def test_flags_tiny_ungrounded_population_like_the_prax_bug():
    cited, plausible, notes = epidemiology_population_flags(4_000, [])
    assert not cited and not plausible
    assert any("NOT cited to an epidemiology source" in n for n in notes)
    assert any("SANITY FLAG" in n for n in notes)


def test_flags_small_but_orphanet_grounded_population_is_plausible():
    # A small number is legitimate when it rests on a rare-disease prevalence source.
    cited, plausible, _ = epidemiology_population_flags(4_000, [_ev("orphanet")])
    assert cited and plausible


def test_plausibility_floor_is_the_boundary():
    below, _, _ = epidemiology_population_flags(POP_PLAUSIBILITY_FLOOR - 1, [_ev("pubmed")])
    at, plausible_at, _ = epidemiology_population_flags(POP_PLAUSIBILITY_FLOOR, [_ev("pubmed")])
    assert below is True  # still cited to epi
    assert plausible_at is True


# ------------------------------------------------- over-aggregation guard (KYMR bug)


def test_flags_over_aggregated_multi_indication_population():
    # KYMR-style: atopic dermatitis summed with asthma/COPD/EoE/CRSwNP into one number.
    cited, plausible, notes = epidemiology_population_flags(
        16_500_000,
        [_ev("pubmed")],
        text="atopic dermatitis + asthma + COPD + EoE and CRSwNP, illustrative umbrella",
    )
    assert cited  # it does cite an epi source
    assert not plausible  # but the population is an over-aggregation
    assert any("aggregates multiple distinct indications" in n for n in notes)


def test_single_indication_plausible_population_is_not_flagged():
    # Atopic dermatitis sized on its own, in-band, cited: no over-aggregation flag.
    cited, plausible, notes = epidemiology_population_flags(
        6_500_000,
        [_ev("pubmed")],
        text="moderate-to-severe atopic dermatitis in US adults",
    )
    assert cited and plausible
    assert not any("aggregates multiple distinct indications" in n for n in notes)


def test_flags_implausibly_large_single_indication_via_ceiling():
    # Above the ceiling is flagged even for a single named indication.
    cited, plausible, notes = epidemiology_population_flags(
        POP_PLAUSIBILITY_CEILING + 1, [_ev("pubmed")], text="atopic dermatitis"
    )
    assert cited
    assert not plausible
    assert any("implausibly large" in n for n in notes)


def test_ceiling_boundary_is_plausible():
    _, at_ceiling, _ = epidemiology_population_flags(
        POP_PLAUSIBILITY_CEILING, [_ev("pubmed")], text="essential tremor"
    )
    assert at_ceiling is True


def test_two_indications_without_summation_language_is_not_over_aggregated():
    # Two indications named (essential tremor, epilepsy) but no summation cue: sizing one
    # of them is fine, so the guard must not trip on mere co-occurrence.
    _, plausible, notes = epidemiology_population_flags(
        7_000_000,
        [_ev("pubmed")],
        text="essential tremor, not to be confused with epilepsy, US prevalence",
    )
    assert plausible
    assert not any("aggregates multiple distinct indications" in n for n in notes)


# ----------------------------------------------------------- epi-retrieval eval


def test_epi_retrieval_surfaces_epidemiology_for_lead_indication(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="pubmed", doc_type="epi", doc_id="EPI1",
        title="focal epilepsy prevalence", url="u",
        text=("Focal epilepsy affects approximately 1.2 million adults in the United "
              "States; the prevalence and incidence of drug-resistant seizures among "
              "these patients defines the addressable population."),
    ))
    retriever = build_retriever("TEST", storage=storage)
    report = EpiRetrievalEvaluator(_reference(), retriever, k=8).run()
    assert report.aggregate["epi_for_indication_any"] == 1.0


def test_epi_retrieval_reports_gap_when_no_epi_evidence(tmp_path):
    storage = Storage(root=tmp_path)
    # Corpus has only unrelated pricing text: no epidemiology for the lead indication.
    storage.write_document(Document(
        company="TEST", source="cms", doc_type="spending", doc_id="P1",
        title="pricing", url="u",
        text="average spending per dosage unit wholesale acquisition cost comparator drug",
    ))
    retriever = build_retriever("TEST", storage=storage)
    report = EpiRetrievalEvaluator(_reference(), retriever, k=8).run()
    assert report.aggregate["epi_for_indication_any"] == 0.0


# ----------------------------------------------------------- epi-grounding eval


def _analysis(statement, value, evidence):
    claim = Claim(statement=statement, confidence=0.5, rationale="", evidence=evidence,
                  value=value)
    return AnalysisResult(company="TEST", module="peak_sales", summary="", claims=[claim])


def test_epi_grounding_passes_for_grounded_in_range_population():
    analysis = _analysis(
        "epidemiology_population = 500000.0 patients [basis: published literature, grounded]",
        "500000.0 patients", [_ev("pubmed")])
    report = EpiGroundingEvaluator(analysis, _reference()).run()
    assert report.aggregate == {
        "cited_epi_source": 1.0, "plausible_magnitude": 1.0, "in_expected_range": 1.0,
    }


def test_epi_grounding_fails_for_tiny_ungrounded_population():
    analysis = _analysis(
        "epidemiology_population = 4000.0 patients (ASSUMED) [basis: not in corpus, assumption]",
        "4000.0 patients", [])
    report = EpiGroundingEvaluator(analysis, _reference()).run()
    assert report.aggregate == {
        "cited_epi_source": 0.0, "plausible_magnitude": 0.0, "in_expected_range": 0.0,
    }


def test_epi_grounding_handles_missing_population_claim():
    analysis = AnalysisResult(company="TEST", module="peak_sales", summary="", claims=[])
    report = EpiGroundingEvaluator(analysis, _reference()).run()
    assert report.aggregate["in_expected_range"] == 0.0


# ----------------------------------------------------------- reference loading


def test_load_prax_reference_names_focal_epilepsy():
    reference = load_epi_reference("PRAX")
    assert reference.company == "PRAX"
    assert "epilepsy" in reference.lead_indication.lower()
    assert reference.plausible_population_min >= 100_000


# All five companies now have a gold epi reference with a sane band and gold docs.
@pytest.mark.parametrize("company,indication_kw,gold_doc", [
    ("ABVX", "ulcerative colitis", "17904915"),
    ("KYMR", "atopic dermatitis", "30389491"),
    ("IMVT", "graves", "22644837"),
    ("COGT", "mastocytosis", "orpha2467"),
    ("PRAX", "tremor", "20175185"),
])
def test_all_five_epi_references_load_and_are_well_formed(company, indication_kw, gold_doc):
    ref = load_epi_reference(company)
    assert ref.company == company
    assert indication_kw in ref.lead_indication.lower()
    # A sane, non-empty band with the floor below the ceiling.
    assert 0 < ref.plausible_population_min < ref.plausible_population_max
    # The authoritative ingested source is referenced for retrieval recall.
    assert gold_doc in ref.relevant_doc_ids
    assert ref.indication_terms and ref.epi_terms
