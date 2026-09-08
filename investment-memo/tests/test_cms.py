"""Tests for the CMS Medicare Part D Spending by Drug ingester.

The live test hits the real data.cms.gov data-api for a marketed comparator
(Dupixent / dupilumab) and asserts real spending rows come back. It is marked
``network`` and skips cleanly when CMS is unreachable (including an IP/edge block),
so the suite still passes offline. The offline tests exercise the wide-row pivot,
name matching, and empty-result handling with an injected fake HTTP client.

Run the live test with a network::

    pytest tests/test_cms.py -m network
"""

from __future__ import annotations

import json
from urllib import error as urlerror

import pytest

from memo.ingestion.cms import CmsSpendingIngester

# One realistic wide Part D row (aggregate "Overall"), shaped like the data-api output:
# year-suffixed metric columns, values as strings, one suppressed year.
_DUPIXENT_ROW = {
    "Brnd_Name": "Dupixent", "Gnrc_Name": "Dupilumab",
    "Tot_Mftr": "1", "Mftr_Name": "Overall",
    "Tot_Spndng_2023": "2,500,000,000.00", "Tot_Dsg_Unts_2023": "800000",
    "Tot_Clms_2023": "150000", "Tot_Benes_2023": "52000",
    "Avg_Spnd_Per_Dsg_Unt_Wghtd_2023": "38.10",
    "Avg_Spnd_Per_Clm_2023": "3900.00", "Avg_Spnd_Per_Bene_2023": "48000.00",
    "Tot_Spndng_2024": "3,987,654,321.00", "Tot_Dsg_Unts_2024": "987654",
    "Tot_Clms_2024": "210000", "Tot_Benes_2024": "71000",
    "Avg_Spnd_Per_Dsg_Unt_Wghtd_2024": "41.20",
    "Avg_Spnd_Per_Clm_2024": "4200.00", "Avg_Spnd_Per_Bene_2024": "56150.00",
    "Tot_Spndng_2022": "", "Avg_Spnd_Per_Dsg_Unt_Wghtd_2022": "",  # suppressed year
}
_NOISE_ROW = {
    "Brnd_Name": "Unrelated Drug", "Gnrc_Name": "somethingelse", "Mftr_Name": "Overall",
    "Tot_Spndng_2024": "10.00", "Avg_Spnd_Per_Dsg_Unt_Wghtd_2024": "1.00",
}


class _FakeHttp:
    """Stand-in for HttpClient: returns a fixed page regardless of URL/keyword."""

    def __init__(self, rows):
        self.rows = rows

    def get_json(self, url, headers=None):
        return list(self.rows)


# ------------------------------------------------------------------ offline

def test_source_key():
    assert CmsSpendingIngester(http=_FakeHttp([])).source == "cms"


def test_pivots_wide_row_to_year_documents():
    ing = CmsSpendingIngester(http=_FakeHttp([_DUPIXENT_ROW, _NOISE_ROW]))
    docs = ing.fetch("KYMR", drugs=["Dupixent", "dupilumab"])

    # 2023 + 2024 for Dupixent; 2022 suppressed; noise row filtered out; deduped
    # across the brand and generic lookups.
    assert len(docs) == 2
    by_year = {d.metadata["year"]: d for d in docs}
    assert set(by_year) == {"2023", "2024"}

    doc = by_year["2024"]
    assert doc.source == "cms"
    assert doc.doc_type == "spending"
    assert doc.doc_id == "Dupixent_2024"
    assert doc.published == "2024-01-01"  # bare year normalized by Document (metadata.year keeps "2024")
    assert isinstance(doc.metadata["total_spending"], float)
    assert doc.metadata["total_spending"] == pytest.approx(3987654321.0)
    # the net-price field
    assert doc.metadata["avg_spending_per_dosage_unit"] == pytest.approx(41.20)
    assert "Dupixent" in doc.title and "2024" in doc.title
    assert doc.text.strip()
    assert "per dosage unit" in doc.text
    assert json.loads(doc.raw)["Brnd_Name"] == "Dupixent"


def test_generic_name_match():
    ing = CmsSpendingIngester(http=_FakeHttp([_DUPIXENT_ROW]))
    docs = ing.fetch("KYMR", drugs=["dupilumab"])
    assert docs and all(d.metadata["generic_name"].lower() == "dupilumab" for d in docs)


def test_no_drugs_returns_empty_without_raising():
    ing = CmsSpendingIngester(http=_FakeHttp([_DUPIXENT_ROW]))
    assert ing.fetch("KYMR") == []
    assert ing.fetch("KYMR", drugs=[]) == []


def test_no_match_returns_empty_without_raising():
    ing = CmsSpendingIngester(http=_FakeHttp([_DUPIXENT_ROW]))
    assert ing.fetch("KYMR", drugs=["Zzznotarealdrugxyz"]) == []


# ------------------------------------------------------------------ live

@pytest.mark.network
def test_fetch_dupixent_live():
    """A real marketed comparator returns >=1 row with a numeric spending figure."""
    ing = CmsSpendingIngester()
    try:
        docs = ing.fetch("KYMR", drugs=["Dupixent", "dupilumab"])
    except (urlerror.URLError, RuntimeError, TimeoutError, OSError) as exc:
        pytest.skip(f"CMS data.cms.gov unreachable: {exc}")

    assert docs, "expected at least one Part D spending row for Dupixent/dupilumab"
    for doc in docs:
        assert doc.source == "cms"
        assert doc.doc_type == "spending"
        assert doc.text.strip(), f"empty text for {doc.doc_id}"
        assert doc.raw.strip(), f"empty raw for {doc.doc_id}"
        assert isinstance(doc.metadata["total_spending"], float)
        assert doc.metadata["total_spending"] > 0

    assert any(d.metadata.get("avg_spending_per_dosage_unit") for d in docs)


@pytest.mark.network
def test_no_match_company_live_returns_empty():
    """A name with no CMS match returns [] without raising, on the live API."""
    ing = CmsSpendingIngester()
    try:
        docs = ing.fetch("KYMR", drugs=["Zzznotarealdrugxyz123"])
    except (urlerror.URLError, RuntimeError, TimeoutError, OSError) as exc:
        pytest.skip(f"CMS data.cms.gov unreachable: {exc}")
    assert docs == []
