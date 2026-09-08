"""Network-backed tests for the NADAC (Medicaid) ingester.

The live tests hit the real DKAN datastore on data.medicaid.gov. They are marked
``network`` and skip cleanly when the network is unavailable, so the suite still
passes offline. Run just these with a network with::

    pytest tests/test_nadac.py -m network
"""

from __future__ import annotations

from urllib import error as urlerror

import pytest

from memo.ingestion.nadac import NadacIngester, _as_str_list, _to_float


def test_no_drugs_returns_empty_without_raising():
    """Default (no ``drugs`` option) is a no-op: empty list, no exception, no network."""
    ingester = NadacIngester()
    assert ingester.fetch("KYMR") == []
    assert ingester.fetch("KYMR", drugs=[]) == []


def test_helpers():
    """String-list coercion and float parsing behave safely."""
    assert _as_str_list(None) == []
    assert _as_str_list("metformin") == ["metformin"]
    assert _as_str_list(["  a ", "", "b"]) == ["a", "b"]
    assert _to_float("0.01419") == pytest.approx(0.01419)
    assert _to_float("") is None
    assert _to_float(None) is None
    assert _to_float("not-a-number") is None


@pytest.mark.network
def test_fetch_metformin_live():
    """A common generic returns priced rows with numeric per-unit cost and text."""
    ingester = NadacIngester()
    try:
        documents = ingester.fetch("KYMR", drugs=["metformin"], limit=25)
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"data.medicaid.gov unreachable: {exc}")

    assert documents, "expected at least one NADAC row for metformin"

    priced = 0
    for doc in documents:
        assert doc.source == "nadac"
        assert doc.doc_type == "nadac_price"
        assert doc.doc_id
        assert doc.text.strip(), f"empty text for {doc.doc_id}"
        assert doc.raw.strip(), f"empty raw for {doc.doc_id}"
        assert doc.url.startswith("https://data.medicaid.gov/dataset/")
        per_unit = doc.metadata.get("nadac_per_unit")
        if isinstance(per_unit, (int, float)):
            priced += 1

    assert priced >= 1, "expected at least one row with a numeric NADAC per-unit cost"


@pytest.mark.network
def test_fetch_no_match_returns_empty_live():
    """A description that matches nothing returns [] without raising."""
    ingester = NadacIngester()
    try:
        documents = ingester.fetch("KYMR", drugs=["zzzznotadrugqq"], limit=5)
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"data.medicaid.gov unreachable: {exc}")
    assert documents == []
