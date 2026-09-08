"""Tests for the Medicare Part B ASP pricing ingester.

The unit tests parse a small captured-shape Payment Limit CSV fixture with no
network, and verify the ASP = payment_limit / 1.06 computation, the effective-
quarter parsing, and the case-insensitive substring match. A separate live test
is marked ``@pytest.mark.network`` and self-skips offline. There are no mock LLMs
here: this is deterministic numeric ingestion, no model is involved.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib import error as urlerror

import pytest

from memo.ingestion.asp import (
    ASP_FILES_PAGE,
    ASP_MARKUP,
    AspFile,
    AspIngester,
    parse_pricing_csv,
)
from memo.ingestion.base import Storage

FIXTURE_CSV = (Path(__file__).parent / "fixtures" / "asp_pricing_sample.csv").read_text()

# A stand-in for the resolved July 2026 pricing file.
FIXTURE_FILE = AspFile(
    url="https://www.cms.gov/files/zip/july-2026-medicare-part-b-payment-limit-files.zip",
    filename="july-2026-medicare-part-b-payment-limit-files.zip",
    month=7,
    year=2026,
    quarter=3,
)


def _ingester_for_fixture() -> AspIngester:
    """An ingester whose pricing table is the fixture (no page fetch, no download)."""
    ing = AspIngester()
    table = parse_pricing_csv(FIXTURE_CSV, FIXTURE_FILE)
    ing._table_cache = {FIXTURE_FILE.url: table}
    # Route any resolution straight to the pre-seeded cache entry.
    ing._resolve_latest_file = lambda: FIXTURE_FILE  # type: ignore[assignment]
    return ing


# ------------------------------------------------------------------ unit tests


def test_parse_pricing_csv_effective_window_and_note():
    table = parse_pricing_csv(FIXTURE_CSV, FIXTURE_FILE)
    assert table.effective_start == "2026-07-01"
    assert table.effective_end == "2026-09-30"
    assert table.asp_data_note == "1Q26"
    assert table.source.quarter_label == "2026 Q3"
    # Six priced HCPCS rows in the fixture; preamble/blank lines excluded.
    assert len(table.records) == 6
    codes = {r.hcpcs for r in table.records}
    assert codes == {"J0517", "J1299", "J1745", "J2356", "J3380", "J9332"}


def test_asp_is_payment_limit_over_1_06():
    table = parse_pricing_csv(FIXTURE_CSV, FIXTURE_FILE)
    by_code = {r.hcpcs: r for r in table.records}

    efg = by_code["J9332"]
    assert efg.payment_limit == 32.553
    # ASP per 2 MG billing unit = 32.553 / 1.06.
    assert efg.asp_per_unit == pytest.approx(32.553 / 1.06)
    assert efg.asp_per_unit == pytest.approx(30.7104, abs=1e-4)
    assert ASP_MARKUP == 1.06

    ecu = by_code["J1299"]
    assert ecu.asp_per_unit == pytest.approx(44.198 / 1.06)
    assert ecu.dosage == "2 MG"


def test_substring_match_is_case_insensitive_and_emits_documents():
    ing = _ingester_for_fixture()
    docs = ing.fetch("IMVT", drugs=["EFGARTIGIMOD", "Eculizumab"])
    by_drug = {d.metadata["drug_query"]: d for d in docs}
    assert set(by_drug) == {"EFGARTIGIMOD", "Eculizumab"}

    efg = by_drug["EFGARTIGIMOD"]
    assert efg.source == "asp"
    assert efg.doc_type == "asp_price"
    assert efg.company == "IMVT"
    assert efg.metadata["hcpcs"] == "J9332"
    assert efg.metadata["payment_limit"] == 32.553
    assert efg.metadata["asp_per_unit"] == pytest.approx(32.553 / 1.06)
    assert efg.metadata["dosage"] == "2 MG"
    assert efg.metadata["quarter"] == "2026 Q3"
    assert efg.published == "2026-07-01"
    # doc_id is deterministic: HCPCS + effective start.
    assert efg.doc_id == "J9332_2026-07-01"
    # Searchable text carries the BM25 anchors.
    low = efg.text.lower()
    assert "asp" in low
    assert "average sales price" in low
    assert "net price" in low
    assert "efgartigimod" in low


def test_curated_comparators_used_when_no_drugs_option():
    """KYMR's curated respiratory biologics resolve against the fixture."""
    ing = _ingester_for_fixture()
    docs = ing.fetch("KYMR")  # curated: tezepelumab, mepolizumab, benralizumab, omalizumab
    codes = {d.metadata["hcpcs"] for d in docs}
    # tezepelumab (J2356) and benralizumab (J0517) are in the fixture; the others
    # are simply not present in this small fixture and are correctly omitted.
    assert codes == {"J2356", "J0517"}


def test_oral_only_pipeline_fetches_nothing():
    """COGT / PRAX comparators are oral (Part D); ASP fetch yields no documents."""
    ing = _ingester_for_fixture()
    assert ing.fetch("COGT") == []
    assert ing.fetch("PRAX") == []


def test_absent_comparator_is_omitted_not_fabricated():
    """A drug not in the file (adalimumab, dupilumab: Part D self-admin) yields nothing."""
    ing = _ingester_for_fixture()
    assert ing.fetch("ABVX", drugs=["adalimumab"]) == []
    assert ing.fetch("KYMR", drugs=["dupilumab"]) == []


def test_run_writes_documents_to_disk():
    with tempfile.TemporaryDirectory() as tmp:
        ing = _ingester_for_fixture()
        ing.storage = Storage(root=Path(tmp))
        manifest = ing.run("IMVT", drugs=["efgartigimod"])
        assert not manifest.errors
        assert manifest.document_count == 1
        asp_dir = Path(tmp) / "IMVT" / "asp"
        assert (asp_dir / "_manifest.json").exists()
        assert (asp_dir / "J9332_2026-07-01.json").exists()
        assert (asp_dir / "J9332_2026-07-01.txt").exists()


def test_page_constant_points_at_reachable_host():
    # data.cms.gov is unreachable from here; the file host must be www.cms.gov.
    assert ASP_FILES_PAGE.startswith("https://www.cms.gov/")


# --------------------------------------------------------------- network test


@pytest.mark.network
def test_fetch_imvt_live():
    """End-to-end run against the live CMS ASP pricing files for IMVT."""
    with tempfile.TemporaryDirectory() as tmp:
        ing = AspIngester(storage=Storage(root=Path(tmp)))
        try:
            docs = ing.fetch("IMVT")
        except (urlerror.URLError, RuntimeError) as exc:
            pytest.skip(f"network unavailable: {exc}")

        assert docs, "expected at least one comparator match (efgartigimod)"
        by_code = {d.metadata["hcpcs"]: d for d in docs}
        assert "J9332" in by_code  # efgartigimod / Vyvgart
        efg = by_code["J9332"]
        assert efg.source == "asp"
        assert efg.metadata["payment_limit"] > 0
        assert efg.metadata["asp_per_unit"] == pytest.approx(
            efg.metadata["payment_limit"] / ASP_MARKUP
        )
        assert efg.url.startswith("https://www.cms.gov/")
        assert efg.raw


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
