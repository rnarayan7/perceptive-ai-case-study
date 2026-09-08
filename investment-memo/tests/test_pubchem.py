"""Network-backed tests for the PubChem PUG-REST ingester.

The live tests hit the real PUG-REST endpoints. They are marked ``network`` and skip
cleanly when the network is unavailable, so the suite still passes offline. Run just
these with a network with::

    pytest tests/test_pubchem.py -m network
"""

from __future__ import annotations

from urllib import error as urlerror

import pytest

from memo.ingestion.pubchem import PubChemIngester


def test_name_override_and_dedup():
    """A ``name`` override wins; ``compounds`` de-duplicates case-insensitively."""
    ingester = PubChemIngester()
    assert ingester._resolve_names("ANY", {"name": "aspirin", "compounds": ["x"]}) == ["aspirin"]
    names = ingester._resolve_names("ANY", {"compounds": ["Aspirin", "aspirin", "caffeine"]})
    assert names == ["Aspirin", "caffeine"]
    assert ingester._resolve_names("ANY", {}) == []  # no compounds, no ingested trials


@pytest.mark.network
def test_fetch_known_compound_live():
    """A name that definitely resolves returns a Document with CID and properties."""
    ingester = PubChemIngester()
    try:
        documents = ingester.fetch("TEST", name="aspirin")
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"PubChem unreachable: {exc}")

    assert len(documents) == 1
    doc = documents[0]
    assert doc.source == "pubchem"
    assert doc.doc_type == "compound"
    assert doc.doc_id == "2244"  # aspirin's stable PubChem CID
    assert doc.url == "https://pubchem.ncbi.nlm.nih.gov/compound/2244"
    assert doc.metadata["molecular_formula"] == "C9H8O4"
    assert doc.metadata["smiles"], "expected a SMILES string"
    assert doc.metadata["synonyms"], "expected at least one synonym"
    assert "C9H8O4" in doc.text
    assert "PubChem CID: 2244" in doc.text
    assert doc.raw.strip(), "raw payload should be populated"


@pytest.mark.network
def test_unresolvable_name_is_skipped_live():
    """A nonsense name yields [] without raising (404 handled gracefully)."""
    ingester = PubChemIngester()
    try:
        documents = ingester.fetch("TEST", name="zzzznotacompoundxyz123qqq")
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"PubChem unreachable: {exc}")

    assert documents == []


@pytest.mark.network
def test_mixed_resolvable_and_not_live():
    """A resolvable name plus an unresolvable one returns only the resolvable compound."""
    ingester = PubChemIngester()
    try:
        documents = ingester.fetch("TEST", compounds=["acetaminophen", "notarealcompoundzzz999"])
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"PubChem unreachable: {exc}")

    assert len(documents) == 1
    assert documents[0].metadata["molecular_formula"] == "C8H9NO2"
