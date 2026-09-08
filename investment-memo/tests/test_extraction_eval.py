"""Offline tests for the extraction / factuality evaluator (deterministic, no network/model).

Writes synthetic ClinicalTrials.gov and EDGAR documents to a tmp Storage, derives gold
from them, and checks that resolution + scoring behave: exact_match=1 when the resolved
value matches, 0 (with expected/actual in detail) when a value is perturbed, and a
numeric-tolerance case that passes within tolerance and fails outside it.
"""

from __future__ import annotations

from dataclasses import replace

from memo.eval.extraction import (
    ExtractionCase,
    ExtractionEvaluator,
    ExtractionGoldSet,
    derive_gold,
    load_extraction_gold,
)
from memo.ingestion.base import Document, Storage
from memo.rag.structured import StructuredStore


def _trial(storage, doc_id, *, enrollment, phases, status, pcd):
    storage.write_document(
        Document(
            company="TEST",
            source="clinicaltrials",
            doc_type="study",
            doc_id=doc_id,
            title=f"Study {doc_id}",
            url=f"https://clinicaltrials.gov/study/{doc_id}",
            metadata={
                "phases": phases,
                "overall_status": status,
                "enrollment": enrollment,
                "primary_completion_date": pcd,
                "interventions": [{"name": "DRUG-X", "type": "DRUG"}],
            },
        )
    )


def _filing(storage, doc_id, *, accession, form, filing_date):
    storage.write_document(
        Document(
            company="TEST",
            source="edgar",
            doc_type=form,
            doc_id=doc_id,
            title=f"{form} {accession}",
            url=f"https://sec.gov/{accession}",
            published=filing_date,
            metadata={"accession": accession, "form": form, "filing_date": filing_date},
        )
    )


def _corpus(tmp_path):
    storage = Storage(root=tmp_path)
    _trial(storage, "NCT001", enrollment=154, phases=["PHASE1"], status="COMPLETED", pcd="2022-10-20")
    _trial(storage, "NCT002", enrollment=56, phases=["PHASE1", "PHASE2"], status="RECRUITING", pcd="2025-03-03")
    _filing(storage, "000199-cover", accession="0001-99", form="8-K", filing_date="2026-04-30")
    _filing(storage, "000199-item2", accession="0001-99", form="8-K", filing_date="2026-04-30")  # same accession, segmented
    _filing(storage, "000200", accession="0002-00", form="10-Q", filing_date="2026-08-05")
    return storage


# --------------------------------------------------------------------- derivation


def test_derive_gold_emits_expected_values(tmp_path):
    storage = _corpus(tmp_path)
    gold = derive_gold("TEST", storage, root=tmp_path / "evals", write=False)
    by_id = {c.id: c for c in gold.cases}

    # Trial fields resolved to their authoritative values.
    assert by_id["NCT001-enrollment"].expected == 154
    assert by_id["NCT001-enrollment"].tolerance == 0.0  # numeric case
    assert by_id["NCT001-phase"].expected == "PHASE1"
    assert by_id["NCT002-phase"].expected == "PHASE1|PHASE2"
    assert by_id["NCT001-overall_status"].expected == "COMPLETED"
    assert by_id["NCT001-primary_completion_date"].expected == "2022-10-20"

    # Filing fields.
    assert by_id["0001-99-form"].expected == "8-K"
    assert by_id["0002-00-filing_date"].expected == "2026-08-05"

    # Filings are de-duplicated to one case per accession despite segmentation.
    form_cases = [c for c in gold.cases if c.field == "filing.form"]
    assert len(form_cases) == 2  # two distinct accessions, not three docs


def test_derive_gold_skips_missing_fields(tmp_path):
    storage = Storage(root=tmp_path)
    # A trial with no phases and no enrollment: those fields must not become gold cases.
    _trial(storage, "NCT003", enrollment=None, phases=[], status="UNKNOWN", pcd=None)
    gold = derive_gold("TEST", storage, root=tmp_path / "evals", write=False)
    ids = {c.id for c in gold.cases}
    assert "NCT003-enrollment" not in ids
    assert "NCT003-phase" not in ids
    assert "NCT003-primary_completion_date" not in ids
    assert "NCT003-overall_status" in ids  # status present -> case emitted


def test_derive_gold_writes_file(tmp_path):
    storage = _corpus(tmp_path)
    gold = derive_gold("TEST", storage, root=tmp_path / "evals", write=True)
    path = tmp_path / "evals" / "extraction" / "TEST.json"
    assert path.exists()
    reloaded = load_extraction_gold(path)
    assert reloaded.company == "TEST"
    assert len(reloaded.cases) == len(gold.cases)


# --------------------------------------------------------------------- scoring


def test_all_cases_match_against_own_corpus(tmp_path):
    storage = _corpus(tmp_path)
    gold = derive_gold("TEST", storage, root=tmp_path / "evals", write=False)
    report = ExtractionEvaluator(gold, StructuredStore("TEST", storage)).run()
    assert report.aggregate["exact_match"] == 1.0
    assert report.aggregate["failed"] == 0.0
    assert report.aggregate["cases"] == float(len(gold.cases))
    for case in report.case_results:
        assert case.metrics["exact_match"] == 1.0


def test_perturbed_value_fails_with_detail(tmp_path):
    storage = _corpus(tmp_path)
    # Gold claims the wrong phase; the store still holds the true value.
    bad = ExtractionCase(
        id="NCT001-phase",
        question="What is the trial phase for trial NCT001?",
        field="trial.phase",
        expected="PHASE3",  # perturbed: store has PHASE1
        locator={"kind": "trial", "nct_id": "NCT001", "attribute": "phase"},
    )
    gold = ExtractionGoldSet(company="TEST", cases=[bad])
    report = ExtractionEvaluator(gold, StructuredStore("TEST", storage)).run()
    result = report.case_results[0]
    assert result.metrics["exact_match"] == 0.0
    assert result.detail["expected"] == "PHASE3"
    assert result.detail["actual"] == "PHASE1"
    assert report.aggregate["failed"] == 1.0


def test_unresolvable_locator_is_a_miss(tmp_path):
    storage = _corpus(tmp_path)
    gold = ExtractionGoldSet(
        company="TEST",
        cases=[
            ExtractionCase(
                id="ghost",
                question="enrollment for a trial not in the corpus",
                field="trial.enrollment",
                expected=100,
                locator={"kind": "trial", "nct_id": "NCT999", "attribute": "enrollment"},
                tolerance=0.0,
            )
        ],
    )
    report = ExtractionEvaluator(gold, StructuredStore("TEST", storage)).run()
    result = report.case_results[0]
    assert result.metrics["exact_match"] == 0.0
    assert result.detail["actual"] == ""  # nothing resolved


# --------------------------------------------------------------------- tolerance


def test_numeric_tolerance(tmp_path):
    storage = _corpus(tmp_path)
    store = StructuredStore("TEST", storage)  # NCT001 enrollment == 154

    within = ExtractionGoldSet(
        company="TEST",
        cases=[
            ExtractionCase(
                id="tol-ok",
                question="enrollment within tolerance",
                field="trial.enrollment",
                expected=152,  # off by 2
                locator={"kind": "trial", "nct_id": "NCT001", "attribute": "enrollment"},
                tolerance=5,
            )
        ],
    )
    assert ExtractionEvaluator(within, store).run().case_results[0].metrics["exact_match"] == 1.0

    outside = ExtractionGoldSet(company="TEST", cases=[replace(within.cases[0], id="tol-bad", tolerance=1)])
    outside_result = ExtractionEvaluator(outside, store).run().case_results[0]
    assert outside_result.metrics["exact_match"] == 0.0  # off by 2, tolerance 1
    assert outside_result.detail["actual"] == "154"
