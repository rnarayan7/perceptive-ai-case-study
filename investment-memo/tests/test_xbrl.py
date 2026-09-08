"""Tests for the SEC XBRL structured-facts ingester (companyfacts API).

The unit tests parse a small captured-shape companyfacts fixture with no network.
A separate live test is marked ``@pytest.mark.network`` and self-skips offline.
There are no mock LLMs here: this is deterministic numeric ingestion, no model
is involved.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib import error as urlerror

import pytest

from memo.ingestion.base import Storage
from memo.ingestion.xbrl import (
    CONCEPTS,
    DERIVED_CASH_STI_KEY,
    XbrlIngester,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "xbrl_companyfacts_sample.json").read_text()
)


def _ingester_for(companyfacts: dict, ticker: str = "TEST") -> XbrlIngester:
    """An ingester wired to a fixture: no ticker fetch, no companyfacts fetch."""
    ing = XbrlIngester()
    ing._ticker_map = {ticker: {"cik": companyfacts["cik"], "title": "Fixture Co"}}
    ing._load_companyfacts = lambda cik: companyfacts  # type: ignore[assignment]
    return ing


# ------------------------------------------------------------------ unit tests


def test_us_gaap_facts_parsed_with_most_recent_values():
    ing = _ingester_for(FIXTURE["us_gaap"])
    docs = {d.doc_id: d for d in ing.fetch("TEST")}

    # Cash picks the latest period end (2026-06-30), not the older 10-K value.
    cash = docs["cash_and_equivalents"]
    assert cash.metadata["value"] == 412000000
    assert cash.metadata["unit"] == "USD"
    assert cash.metadata["period_end"] == "2026-06-30"
    assert cash.published == "2026-06-30"
    assert cash.metadata["tag"] == "CashAndCashEquivalentsAtCarryingValue"
    assert cash.metadata["accession"] == "0000-26-000002"
    assert cash.metadata["form"] == "10-Q"
    # Searchable sentence names the concept and the value for BM25.
    assert "cash and cash equivalents" in cash.text.lower()
    assert "412" in cash.text

    # Shares outstanding via the dei tag, latest cover date.
    shares = docs["common_shares_outstanding"]
    assert shares.metadata["value"] == 60000000
    assert shares.metadata["unit"] == "shares"
    assert shares.metadata["period_end"] == "2026-07-31"

    # Short-term investments resolves via the MarketableSecuritiesCurrent fallback.
    sti = docs["short_term_investments"]
    assert sti.metadata["tag"] == "MarketableSecuritiesCurrent"
    assert sti.metadata["value"] == 88000000

    # Total liabilities and net loss present.
    assert docs["total_liabilities"].metadata["value"] == 95000000
    assert docs["net_income_loss"].metadata["value"] == -61000000


def test_duration_tie_break_prefers_longest_period():
    """Same end + same filing: the year-to-date R&D wins over the quarter stub."""
    ing = _ingester_for(FIXTURE["us_gaap"])
    docs = {d.doc_id: d for d in ing.fetch("TEST")}
    rnd = docs["research_and_development_expense"]
    assert rnd.metadata["value"] == 78000000  # YTD, not the 40M quarter
    assert rnd.metadata["period_start"] == "2026-01-01"
    assert rnd.metadata["period_end"] == "2026-06-30"
    assert "2026-01-01 to 2026-06-30" in rnd.text


def test_derived_cash_plus_sti_sums_same_date_components():
    ing = _ingester_for(FIXTURE["us_gaap"])
    docs = {d.doc_id: d for d in ing.fetch("TEST")}
    combined = docs[DERIVED_CASH_STI_KEY]
    assert combined.metadata["derived"] is True
    assert combined.metadata["value"] == 412000000 + 88000000
    assert combined.metadata["period_end"] == "2026-06-30"
    comps = {c["concept"] for c in combined.metadata["components"]}
    assert comps == {"cash_and_equivalents", "short_term_investments"}


def test_missing_concepts_are_omitted_not_fabricated():
    ing = _ingester_for(FIXTURE["us_gaap"])
    ids = {d.doc_id for d in ing.fetch("TEST")}
    # This filer reports no long-term debt tag, so no total_debt document exists.
    assert "total_debt" not in ids
    # Every emitted doc is one of the known concept keys (plus the derived one).
    known = {c.key for c in CONCEPTS} | {DERIVED_CASH_STI_KEY}
    assert ids <= known


def test_ifrs_foreign_filer_uses_eur_and_ifrs_tags():
    """ABVX-shaped IFRS/EUR filer: falls back to IFRS tags, records EUR currency."""
    ing = _ingester_for(FIXTURE["ifrs_eur"])
    docs = {d.doc_id: d for d in ing.fetch("TEST")}

    cash = docs["cash_and_equivalents"]
    assert cash.metadata["unit"] == "EUR"
    assert cash.metadata["taxonomy"] == "ifrs-full"
    assert cash.metadata["tag"] == "CashAndCashEquivalents"
    assert cash.metadata["reporting_currency"] == "EUR"

    # Net loss maps to IFRS ProfitLoss.
    net = docs["net_income_loss"]
    assert net.metadata["tag"] == "ProfitLoss"
    assert net.metadata["value"] == -85000000

    # US-GAAP-only concepts (liabilities via us-gaap:Liabilities) are absent, and
    # so is the derived cash+STI (no short-term-investments tag reported).
    assert "total_liabilities" not in docs
    assert DERIVED_CASH_STI_KEY not in docs
    # Shares still come through via dei.
    assert docs["common_shares_outstanding"].metadata["unit"] == "shares"


def test_run_writes_documents_to_disk():
    with tempfile.TemporaryDirectory() as tmp:
        ing = _ingester_for(FIXTURE["us_gaap"])
        ing.storage = Storage(root=Path(tmp))
        manifest = ing.run("TEST")
        assert not manifest.errors
        assert manifest.document_count > 0
        xbrl_dir = Path(tmp) / "TEST" / "xbrl"
        assert (xbrl_dir / "_manifest.json").exists()
        assert (xbrl_dir / "cash_and_equivalents.json").exists()
        assert (xbrl_dir / "cash_and_equivalents.txt").exists()


# --------------------------------------------------------------- network test


@pytest.mark.network
def test_fetch_kymr_live():
    """End-to-end run against live SEC companyfacts for KYMR."""
    with tempfile.TemporaryDirectory() as tmp:
        ing = XbrlIngester(storage=Storage(root=Path(tmp)))
        try:
            docs = ing.fetch("KYMR")
        except (urlerror.URLError, RuntimeError) as exc:
            pytest.skip(f"network unavailable: {exc}")

        assert docs, "expected at least one fact"
        by_id = {d.doc_id: d for d in docs}
        assert "cash_and_equivalents" in by_id
        cash = by_id["cash_and_equivalents"]
        assert cash.source == "xbrl"
        assert cash.company == "KYMR"
        assert cash.metadata["value"] > 0
        assert cash.metadata["unit"] == "USD"
        assert cash.url.startswith("https://www.sec.gov/")
        assert cash.raw


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
