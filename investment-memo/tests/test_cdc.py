"""Network-backed tests for the CDC (data.cdc.gov Socrata) ingester.

The live test hits the real SODA API for the U.S. Chronic Disease Indicators dataset
(resource id ``hksd-2xuw``) with a small ``$limit``, and asserts that at least one row
comes back with populated text and a numeric measure. A second live test confirms that a
bogus ``$where`` yields ``[]`` rather than raising. Both are marked ``network`` and skip
cleanly when the network is unavailable, so the suite still passes offline.

Run just these with a network::

    pytest tests/test_cdc.py -m network
"""

from __future__ import annotations

from urllib import error as urlerror

import pytest

from memo.ingestion.cdc import CdcIngester

_DATASET = "hksd-2xuw"  # U.S. Chronic Disease Indicators


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def test_source_key():
    assert CdcIngester.source == "cdc"


@pytest.mark.network
def test_fetch_asthma_prevalence_live():
    """Default dataset + an asthma prevalence filter returns populated epi rows."""
    ingester = CdcIngester()
    try:
        documents = ingester.fetch(
            "KYMR",
            dataset=_DATASET,
            where="topic='Asthma' AND datavaluetype='Crude Prevalence' AND datavalue IS NOT NULL",
            limit=5,
        )
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"data.cdc.gov unreachable: {exc}")

    assert documents, "expected at least one asthma prevalence row"

    for doc in documents:
        assert doc.source == "cdc"
        assert doc.doc_type == "epi"
        assert doc.doc_id.startswith(f"{_DATASET}-")
        assert doc.url == f"https://data.cdc.gov/d/{_DATASET}"
        assert doc.title.strip(), f"empty title for {doc.doc_id}"
        assert doc.text.strip(), f"empty text for {doc.doc_id}"
        assert doc.raw.strip(), f"empty raw for {doc.doc_id}"

    # At least one row carries a numeric measure value.
    assert any(_is_number(doc.metadata.get("value")) for doc in documents), \
        "expected at least one numeric datavalue"


@pytest.mark.network
def test_default_dataset_and_where_live():
    """With no options, the ingester uses its default dataset and returns epi rows."""
    ingester = CdcIngester()
    try:
        documents = ingester.fetch("KYMR", limit=3)
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"data.cdc.gov unreachable: {exc}")

    assert documents, "expected default asthma rows"
    assert all(doc.metadata.get("dataset") == _DATASET for doc in documents)


@pytest.mark.network
def test_bogus_where_returns_empty_live():
    """A filter matching nothing yields an empty list, never an exception."""
    ingester = CdcIngester()
    try:
        documents = ingester.fetch(
            "KYMR",
            dataset=_DATASET,
            where="locationabbr='ZZ_NOPE'",
            limit=5,
        )
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"data.cdc.gov unreachable: {exc}")

    assert documents == []
