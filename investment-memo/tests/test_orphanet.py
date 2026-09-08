"""Tests for the Orphanet / Orphadata rare-disease prevalence ingester.

The unit tests parse a small captured Orphadata JSON fixture (the epidemiology
ORPHAcode index plus two per-code epidemiology records) with no network. A
separate live test is marked ``@pytest.mark.network`` and self-skips offline.
No LLM is involved: this is deterministic epidemiology ingestion.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib import error as urlerror

import pytest

from memo.ingestion.base import Storage
from memo.ingestion.orphanet import Indication, OrphanetIngester

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "orphanet_epidemiology_sample.json").read_text()
)


def _ingester_for_fixture() -> OrphanetIngester:
    """An ingester wired to the fixture: no index fetch, no per-code fetch."""
    ing = OrphanetIngester()
    ing._epi_index = {
        str(r["Preferred term"]).strip().lower(): r["ORPHAcode"]
        for r in FIXTURE["orphacodes_list"]["data"]["results"]
    }
    by_code = FIXTURE["by_orphacode"]

    def _fetch(orphacode: int):
        record = by_code.get(str(orphacode))
        if record is None:
            return None
        return record["data"]["results"]

    ing._fetch_epidemiology = _fetch  # type: ignore[assignment]
    return ing


# ------------------------------------------------------------------ unit tests


def test_prevalence_records_parsed_one_document_each():
    """Systemic mastocytosis (ORPHA:2467) yields one doc per prevalence entry."""
    ing = _ingester_for_fixture()
    docs = ing.fetch("COGT", disorders=[Indication("Systemic mastocytosis")])

    expected = FIXTURE["by_orphacode"]["2467"]["data"]["results"]["Prevalence"]
    assert len(docs) == len(expected) == 7

    for d in docs:
        assert d.source == "orphanet"
        assert d.doc_type == "prevalence"
        assert d.company == "COGT"
        assert d.metadata["orphacode"] == 2467
        assert d.metadata["preferred_term"] == "Systemic mastocytosis"
        # BM25-facing text names the disease and the epidemiology keywords.
        low = d.text.lower()
        assert "systemic mastocytosis" in low
        assert "prevalence" in low and "epidemiology" in low

    # A specific known entry: point prevalence 1-5 / 10 000 in the Netherlands.
    nl = next(
        d for d in docs
        if d.metadata["geographic_area"] == "Netherlands"
    )
    assert nl.metadata["prevalence_type"] == "Point prevalence"
    assert nl.metadata["prevalence_class"] == "1-5 / 10 000"
    assert nl.metadata["value_average"] == "13.0"
    assert nl.metadata["validation_status"] == "Validated"
    assert nl.metadata["source_pmids"] == ["23219169"]
    assert nl.url.startswith("http")


def test_pmid_parsed_from_compound_source():
    """A source like '17494979[PMID]' is parsed to a bare PMID list."""
    ing = _ingester_for_fixture()
    docs = ing.fetch("IMVT", disorders=[Indication("Myasthenia gravis")])
    # Fixture MG record is trimmed to its first 3 prevalence entries.
    assert len(docs) == 3
    us = next(d for d in docs if d.metadata["geographic_area"] == "United States")
    assert us.metadata["prevalence_type"] == "Annual incidence"
    assert us.metadata["source_pmids"] == ["8909435"]


def test_deterministic_doc_ids_are_unique_and_stable():
    ing = _ingester_for_fixture()
    docs = ing.fetch("COGT", disorders=[Indication("Systemic mastocytosis")])
    ids = [d.doc_id for d in docs]
    assert len(ids) == len(set(ids))  # unique
    assert all(d.doc_id.startswith("orpha2467-") for d in docs)
    # Re-running produces identical ids (deterministic).
    again = ing.fetch("COGT", disorders=[Indication("Systemic mastocytosis")])
    assert [d.doc_id for d in again] == ids


def test_name_resolution_exact_match_and_common_disease_absent():
    ing = _ingester_for_fixture()
    # Resolvable rare disease -> code from the index.
    assert ing._resolve_orphacode(Indication("systemic mastocytosis")) == 2467
    # A common disease (not in the epidemiology index) resolves to nothing.
    assert ing._resolve_orphacode(Indication("Graves disease")) is None


def test_unresolved_disorders_are_reported_not_fabricated():
    ing = _ingester_for_fixture()
    docs = ing.fetch(
        "IMVT",
        disorders=[
            Indication("Chronic inflammatory demyelinating polyneuropathy"),
            Indication("Graves disease"),
            Indication("Thyroid eye disease"),
        ],
    )
    # CIDP is not in this trimmed fixture's by_orphacode, so no docs, and it plus
    # the two common diseases are all reported as unresolved rather than invented.
    assert docs == []
    assert "Graves disease" in ing.unresolved
    assert "Thyroid eye disease" in ing.unresolved
    assert "Chronic inflammatory demyelinating polyneuropathy" in ing.unresolved


def test_pinned_orphacode_bypasses_name_resolution():
    ing = _ingester_for_fixture()
    # Pin to the systemic mastocytosis code under a colloquial name absent from the index.
    docs = ing.fetch(
        "COGT", disorders=[Indication("Advanced SM (colloquial)", orphacode=2467)]
    )
    assert docs
    assert all(d.metadata["orphacode"] == 2467 for d in docs)


def test_run_writes_documents_to_disk():
    with tempfile.TemporaryDirectory() as tmp:
        ing = _ingester_for_fixture()
        ing.storage = Storage(root=Path(tmp))
        manifest = ing.run("COGT", disorders=[Indication("Systemic mastocytosis")])
        assert not manifest.errors
        assert manifest.document_count == 7
        orphanet_dir = Path(tmp) / "COGT" / "orphanet"
        assert (orphanet_dir / "_manifest.json").exists()
        jsons = [p for p in orphanet_dir.glob("*.json") if p.name != "_manifest.json"]
        assert len(jsons) == 7


# --------------------------------------------------------------- network test


@pytest.mark.network
def test_fetch_imvt_live():
    """End-to-end run against live Orphadata for IMVT's rare indications."""
    with tempfile.TemporaryDirectory() as tmp:
        ing = OrphanetIngester(storage=Storage(root=Path(tmp)))
        try:
            docs = ing.fetch("IMVT")
        except (urlerror.URLError, RuntimeError) as exc:
            pytest.skip(f"network unavailable: {exc}")

        assert docs, "expected at least one prevalence record"
        # Myasthenia gravis (ORPHA:589) is a rare disease with prevalence data.
        mg = [d for d in docs if d.metadata["orphacode"] == 589]
        assert mg
        d = mg[0]
        assert d.source == "orphanet"
        assert d.doc_type == "prevalence"
        assert d.metadata["prevalence_type"]
        assert d.metadata["prevalence_class"]
        assert "prevalence" in d.text.lower()
        # Graves' disease / thyroid eye disease are common and not in Orphanet epi.
        assert "Graves disease" in ing.unresolved
        assert "Thyroid eye disease" in ing.unresolved


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
