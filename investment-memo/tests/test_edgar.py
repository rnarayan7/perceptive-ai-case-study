"""Tests for the SEC EDGAR ingester.

The main test is network-backed (it hits live SEC endpoints). It is marked with
``@pytest.mark.network`` and self-skips on any network failure so the suite still
passes offline. Run just this test with ``pytest -m network``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib import error as urlerror

import pytest

from memo.ingestion.base import Storage
from memo.ingestion.edgar import (
    DEFAULT_FORMS,
    EdgarIngester,
    FilingRow,
    FilingSection,
    _HTMLTextExtractor,
    segment_sections,
)

# A compact synthetic 10-Q-style filing: a table of contents that lists the item
# headers, then the body, where item numbers repeat across PART I and PART II.
SYNTHETIC_10Q = """\
KYMERA THERAPEUTICS, INC.
FORM 10-Q

PART I.
FINANCIAL INFORMATION
Item 1.
Financial Statements
1
Item 2.
Management's Discussion and Analysis
25
PART II.
OTHER INFORMATION
Item 1A.
Risk Factors
37

PART I—FINANCIAL INFORMATION
Item 1. Financial Statements.
As of June 30, 2026, we had cash and cash equivalents of $1,505 million.
Item 2. Management's Discussion and Analysis of Financial Condition.
Our operating losses continued during the quarter.

PART II—OTHER INFORMATION
Item 1A. Risk Factors.
We may seek Fast Track Designation for our product candidates.
This risk-factor language repeats quarter over quarter.
"""

# Valid EDGAR form types we expect to see for a biopharma issuer.
VALID_FORM_PREFIXES = ("10-K", "10-Q", "8-K", "S-1", "424B", "20-F", "6-K")


def _looks_valid_form(form: str) -> bool:
    upper = form.upper()
    return any(upper.startswith(p) for p in VALID_FORM_PREFIXES)


# ------------------------------------------------------------------ unit tests


def test_html_to_text_strips_markup_and_scripts():
    html = (
        "<html><head><title>x</title><style>.a{}</style></head>"
        "<body><script>ignore()</script>"
        "<p>Hello world.</p><div>Second line.</div></body></html>"
    )
    text = EdgarIngester._html_to_text(html)
    assert "Hello world." in text
    assert "Second line." in text
    assert "ignore()" not in text
    assert ".a{}" not in text


def test_form_matches_exact_prefix_and_amendment():
    forms = set(DEFAULT_FORMS)
    prefixes = ("424B",)
    assert EdgarIngester._form_matches("10-K", forms, prefixes)
    assert EdgarIngester._form_matches("10-K/A", forms, prefixes)  # amendment
    assert EdgarIngester._form_matches("424B5", forms, prefixes)  # prefix
    assert EdgarIngester._form_matches("20-F", forms, prefixes)  # FPI form
    assert not EdgarIngester._form_matches("SC 13G", forms, prefixes)


def test_iter_recent_filings_unpacks_parallel_arrays():
    ing = EdgarIngester()
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001-24-000001", "0001-24-000002"],
                "form": ["10-K", "8-K"],
                "filingDate": ["2024-02-01", "2024-03-01"],
                "reportDate": ["2023-12-31", ""],
                "primaryDocument": ["a.htm", "b.htm"],
                "primaryDocDescription": ["10-K", "EARNINGS"],
                "items": ["", "2.02"],
            }
        }
    }
    rows = list(ing._iter_recent_filings(submissions))
    assert len(rows) == 2
    assert isinstance(rows[0], FilingRow)
    assert rows[0].form == "10-K"
    assert rows[0].accession_nodash == "000124000001"
    assert rows[1].items == "2.02"


def test_resolve_cik_unknown_ticker_raises():
    ing = EdgarIngester()
    ing._ticker_map = {"KYMR": {"cik": 1815442, "title": "Kymera"}}
    cik, title = ing._resolve_cik("KYMR")
    assert cik == 1815442
    with pytest.raises(ValueError):
        ing._resolve_cik("NOTATICKER")


# ------------------------------------------------------------- segmentation

def test_segment_sections_splits_by_item_headers():
    sections = segment_sections(SYNTHETIC_10Q)
    keys = [s.key for s in sections]
    # Multiple sections with distinct doc_id-suffix keys.
    assert len(keys) == len(set(keys)) >= 4
    # Part disambiguates the two "Item 1"-family headers across parts.
    assert "pi-item1" in keys
    assert "pi-item2" in keys
    assert "pii-item1a" in keys
    # Leading cover / table-of-contents content is preserved, not dropped.
    assert "cover" in keys

    by_key = {s.key: s for s in sections}
    assert by_key["pi-item1"].label == "Financial Statements"
    assert "Management's Discussion" in by_key["pi-item2"].label
    assert by_key["pii-item1a"].label == "Risk Factors"

    # Content landed in the right sections (body, not the table of contents).
    assert "cash and cash equivalents" in by_key["pi-item1"].text
    assert "Fast Track Designation" in by_key["pii-item1a"].text
    # The repeated table-of-contents headers did not create duplicate sections.
    assert keys.count("pi-item1") == 1


def test_segment_sections_flags_risk_factors_boilerplate():
    by_key = {s.key: s for s in segment_sections(SYNTHETIC_10Q)}
    assert by_key["pii-item1a"].boilerplate is True
    assert by_key["pi-item1"].boilerplate is False


def test_segment_sections_falls_back_when_no_headers():
    plain = (
        "This is a press release with no SEC item headers at all.\n"
        "It simply describes a corporate development in prose.\n"
    )
    assert segment_sections(plain) == []


def test_segment_sections_handles_8k_subitems():
    text = (
        "FORM 8-K\n"
        "Item 7.01 Regulation FD Disclosure.\n"
        "The company furnished a presentation.\n"
        "Item 9.01 Financial Statements and Exhibits.\n"
        "Exhibit 99.1 is filed herewith.\n"
    )
    keys = [s.key for s in segment_sections(text)]
    assert "item7_01" in keys
    assert "item9_01" in keys


def test_build_documents_emits_one_document_per_section(monkeypatch):
    ing = EdgarIngester()
    monkeypatch.setattr(
        ing, "_fetch_primary_document", lambda cik, row: ("<html/>", SYNTHETIC_10Q)
    )
    row = FilingRow(
        accession="0001-26-000001",
        form="10-Q",
        filing_date="2026-08-07",
        report_date="2026-06-30",
        primary_document="kymr-10q.htm",
        primary_doc_description="10-Q",
        items="",
    )
    docs = ing._build_documents("KYMR", 1815442, "Kymera Therapeutics", row, want_text=True)

    assert len(docs) >= 4
    doc_ids = [d.doc_id for d in docs]
    assert len(doc_ids) == len(set(doc_ids)), "section doc_ids must be unique"
    assert all(d.doc_id.startswith("000126000001-") for d in docs)
    assert all(d.doc_type == "10-Q" for d in docs)
    assert all(d.metadata.get("section") for d in docs)
    # Original filing-level metadata is retained on every section.
    assert all(d.metadata.get("accession") == "0001-26-000001" for d in docs)

    by_id = {d.doc_id: d for d in docs}
    risk = by_id["000126000001-pii-item1a"]
    assert risk.metadata["section"] == "Risk Factors"
    assert risk.metadata.get("boilerplate") is True
    assert "Fast Track Designation" in risk.text
    assert risk.title.endswith("Risk Factors")


def test_build_documents_single_document_without_sections(monkeypatch):
    ing = EdgarIngester()
    monkeypatch.setattr(
        ing,
        "_fetch_primary_document",
        lambda cik, row: ("<html/>", "A short 8-K press release with no item headers."),
    )
    row = FilingRow(
        accession="0001-26-000002",
        form="8-K",
        filing_date="2026-08-01",
        report_date="",
        primary_document="kymr-8k.htm",
        primary_doc_description="8-K",
        items="7.01",
    )
    docs = ing._build_documents("KYMR", 1815442, "Kymera Therapeutics", row, want_text=True)
    assert len(docs) == 1
    assert docs[0].doc_id == "000126000002"
    assert "section" not in docs[0].metadata


def test_build_documents_metadata_only_stays_single():
    ing = EdgarIngester()
    row = FilingRow(
        accession="0001-26-000003",
        form="8-K",
        filing_date="2026-07-01",
        report_date="",
        primary_document="",
        primary_doc_description="8-K",
        items="",
    )
    docs = ing._build_documents("KYMR", 1815442, "Kymera Therapeutics", row, want_text=False)
    assert len(docs) == 1
    assert docs[0].doc_id == "000126000003"
    assert docs[0].raw, "metadata-only filing should still have a raw payload"


# --------------------------------------------------------------- network test


@pytest.mark.network
def test_fetch_kymr_live():
    """End-to-end run against live SEC EDGAR for KYMR (small limits)."""
    with tempfile.TemporaryDirectory() as tmp:
        ing = EdgarIngester(storage=Storage(root=Path(tmp)))
        try:
            manifest = ing.run("KYMR", limit=5, primary_text_count=2)
        except (urlerror.URLError, RuntimeError) as exc:
            pytest.skip(f"network unavailable: {exc}")

        assert not manifest.errors, manifest.errors
        assert manifest.document_count > 0

        # Re-fetch to inspect full Document objects (text is not in the manifest).
        docs = ing.fetch("KYMR", limit=5, primary_text_count=2)
        assert docs, "expected at least one filing"

        for d in docs:
            assert d.doc_id, "doc_id must be non-empty"
            assert d.source == "edgar"
            assert d.company == "KYMR"
            assert _looks_valid_form(d.doc_type), f"unexpected form {d.doc_type!r}"
            assert d.url.startswith("https://www.sec.gov/")
            assert d.raw, "raw payload should never be empty"

        assert any(d.text.strip() for d in docs), "expected at least one doc with text"

        # Files landed on disk under <root>/KYMR/edgar/.
        edgar_dir = Path(tmp) / "KYMR" / "edgar"
        assert (edgar_dir / "_manifest.json").exists()
        assert list(edgar_dir.glob("*.raw")), "expected raw payloads on disk"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v", "-m", "network"]))
