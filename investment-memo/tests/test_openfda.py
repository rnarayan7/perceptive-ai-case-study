"""Network-backed tests for the openFDA ingester.

openFDA has no record of Kymera's investigational assets, so the live tests use a
real approved comparator (dupilumab, an atopic-dermatitis biologic in KYMR's space)
to prove the client works, and separately assert that a company with no approved
drugs yields an empty list without raising.

The live tests are marked ``network`` and skip cleanly offline. Run them with::

    pytest tests/test_openfda.py -m network
"""

from __future__ import annotations

from urllib import error as urlerror

import pytest

from memo.ingestion.openfda import OpenFdaIngester


def test_no_terms_returns_empty_without_raising():
    """A company with no approved drugs and no comparator terms yields []."""
    ingester = OpenFdaIngester()
    # No network is touched: with neither terms nor indication, fetch short-circuits.
    assert ingester.fetch("KYMR") == []
    assert ingester.fetch("KYMR", terms=[]) == []


def test_option_normalization():
    """Terms/endpoints/limit normalization is defensive and pure."""
    ingester = OpenFdaIngester()
    assert ingester._normalize_terms("dupilumab") == ["dupilumab"]
    assert ingester._normalize_terms(["a", " ", "b"]) == ["a", "b"]
    assert ingester._normalize_terms(None) == []
    assert ingester._normalize_endpoints("label") == {"label"}
    assert ingester._normalize_endpoints(None) == {"label", "adverse_event", "drugsfda"}
    assert ingester._normalize_endpoints(["bogus"]) == {"label", "adverse_event", "drugsfda"}
    assert ingester._clamp_limit("5") == 5
    assert ingester._clamp_limit(9999) == 100
    assert ingester._clamp_limit("bad") == 10


@pytest.mark.network
def test_fetch_dupilumab_label_live():
    """A label lookup for an approved comparator returns populated documents."""
    ingester = OpenFdaIngester()
    try:
        documents = ingester.fetch("KYMR", terms=["dupilumab"], limit=3, endpoints=["label"])
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"openFDA unreachable: {exc}")

    assert documents, "expected at least one dupilumab label"
    for doc in documents:
        assert doc.source == "openfda"
        assert doc.doc_type == "label"
        assert doc.doc_id.strip(), "empty doc_id"
        assert doc.url.strip(), f"empty url for {doc.doc_id}"
        assert doc.text.strip(), f"empty text for {doc.doc_id}"
        assert doc.raw.strip(), f"empty raw for {doc.doc_id}"
    # The label text should carry analyst-relevant fields.
    assert any("Indications" in d.text for d in documents)


@pytest.mark.network
def test_fetch_all_endpoints_live():
    """All three endpoints return typed documents for a well-covered comparator."""
    ingester = OpenFdaIngester()
    try:
        documents = ingester.fetch("KYMR", terms=["dupilumab"], limit=3)
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"openFDA unreachable: {exc}")

    types = {d.doc_type for d in documents}
    assert "label" in types
    assert "adverse_event" in types
    assert "drugsfda" in types
    for doc in documents:
        assert doc.doc_id.strip()
        assert doc.text.strip()
        assert doc.raw.strip()


@pytest.mark.network
def test_investigational_term_returns_empty_live():
    """An investigational Kymera code (no openFDA record) yields [] without raising."""
    ingester = OpenFdaIngester()
    try:
        documents = ingester.fetch("KYMR", terms=["KT-474"], limit=3)
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"openFDA unreachable: {exc}")
    assert documents == []
