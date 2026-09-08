"""Network-backed tests for the ClinicalTrials.gov ingester.

The live test hits the real v2 API for Kymera Therapeutics (KYMR). It is marked
``network`` and skips cleanly when the network is unavailable, so the suite still
passes offline. Run just this test with a network with::

    pytest tests/test_clinicaltrials.py -m network
"""

from __future__ import annotations

import re
from urllib import error as urlerror

import pytest

from memo.ingestion.clinicaltrials import ClinicalTrialsIngester

NCT_RE = re.compile(r"^NCT\d{8}$")


def test_resolve_sponsor_mapping_and_override():
    """Ticker resolution: known mapping, override, and unknown-ticker error."""
    ingester = ClinicalTrialsIngester()
    assert ingester._resolve_sponsor("KYMR", None) == "Kymera Therapeutics"
    assert ingester._resolve_sponsor("kymr", None) == "Kymera Therapeutics"
    assert ingester._resolve_sponsor("ZZZZ", "Custom Sponsor") == "Custom Sponsor"
    with pytest.raises(ValueError):
        ingester._resolve_sponsor("ZZZZ", None)


@pytest.mark.network
def test_fetch_kymr_studies_live():
    """Fetch a small set of KYMR studies and assert the expected shape."""
    ingester = ClinicalTrialsIngester()
    try:
        documents = ingester.fetch("KYMR", limit=20)
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"ClinicalTrials.gov unreachable: {exc}")

    assert documents, "expected at least one study for Kymera Therapeutics"

    for doc in documents:
        assert doc.source == "clinicaltrials"
        assert doc.doc_type == "study"
        assert NCT_RE.match(doc.doc_id), f"bad NCT id: {doc.doc_id!r}"
        assert doc.url == f"https://clinicaltrials.gov/study/{doc.doc_id}"
        assert doc.text.strip(), f"empty text for {doc.doc_id}"
        assert doc.raw.strip(), f"empty raw for {doc.doc_id}"

    # Phase and status should be present on at least some studies.
    assert any(doc.metadata.get("phases") for doc in documents)
    assert any(doc.metadata.get("overall_status") for doc in documents)
