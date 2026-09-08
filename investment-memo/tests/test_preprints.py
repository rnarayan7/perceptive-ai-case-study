"""Network-backed tests for the Europe PMC preprint ingester.

The live test hits the real Europe PMC search API with a term likely to surface
bioRxiv/medRxiv preprints. It is marked ``network`` and self-skips on any network
failure, so the suite still passes offline. Run just this test with::

    pytest tests/test_preprints.py -m network
"""

from __future__ import annotations

from urllib import error as urlerror

import pytest

from memo.ingestion.preprints import PreprintsIngester, _strip_html


def test_strip_html_removes_tags_and_entities():
    """HTML tags and entities are stripped; readable words are preserved."""
    raw = "<h4>ABSTRACT</h4> IRAK4 &amp; STAT3 <i>degraders</i>"
    cleaned = _strip_html(raw)

    assert "<" not in cleaned and ">" not in cleaned, f"tags leaked: {cleaned!r}"
    assert "&amp;" not in cleaned and "&lt;" not in cleaned, f"entity leaked: {cleaned!r}"
    for word in ("ABSTRACT", "IRAK4", "STAT3", "degraders"):
        assert word in cleaned, f"lost word {word!r} in {cleaned!r}"
    # Words on either side of a tag must not fuse together.
    assert "ABSTRACT IRAK4" in cleaned, f"words fused: {cleaned!r}"
    assert cleaned == "ABSTRACT IRAK4 & STAT3 degraders"


def test_strip_html_passes_plain_text_through():
    """Already-plain text is effectively a no-op (only whitespace collapse)."""
    assert _strip_html("IRAK4 degrader in AML") == "IRAK4 degrader in AML"
    assert _strip_html("") == ""


def test_to_document_strips_html_from_abstract():
    """A Europe PMC result with HTML in abstractText yields clean text."""
    ingester = PreprintsIngester()
    result = {
        "id": "PPR123",
        "title": "An IRAK4 degrader in AML",
        "abstractText": "Abstract: <h4>ABSTRACT</h4> IRAK4 &amp; STAT3 <i>degraders</i>",
        "source": "PPR",
    }
    doc = ingester._to_document("KYMR", result)

    assert doc is not None
    assert "<" not in doc.text and ">" not in doc.text, f"tags leaked: {doc.text!r}"
    assert "&amp;" not in doc.text, f"entity leaked: {doc.text!r}"
    assert "h4" not in doc.text.split(), f"junk 'h4' token present: {doc.text!r}"
    assert "Abstract:" in doc.text
    for word in ("ABSTRACT", "IRAK4", "STAT3", "degraders"):
        assert word in doc.text


def test_empty_query_returns_empty_without_raising():
    """An unresolvable company (no codes, no sponsor mapping) yields []."""
    ingester = PreprintsIngester()
    # ZZZZ has no clinical-trials corpus and no sponsor mapping -> "" -> [].
    assert ingester.fetch("ZZZZ") == []


def test_default_term_falls_back_to_sponsor(tmp_path):
    """No codes and no mapping -> ""; a known mapping resolves to the sponsor name.

    Use an isolated empty Storage so ingested trials on disk can't leak in and derive
    asset codes (otherwise this depends on what happens to be ingested). With no trials
    for ABVX, no codes are derived and the query falls back to the sponsor name; an
    unmapped ticker yields "".
    """
    from memo.ingestion.base import Storage

    ingester = PreprintsIngester(storage=Storage(root=tmp_path))
    assert ingester._default_term("ABVX") == "Abivax"
    assert ingester._default_term("zzzz") == ""


@pytest.mark.network
def test_fetch_preprints_live():
    """Fetch preprints for an explicit term and assert the expected shape."""
    ingester = PreprintsIngester()
    try:
        documents = ingester.fetch("KYMR", term="IRAK4 degrader", limit=10)
    except (urlerror.URLError, RuntimeError, TimeoutError) as exc:
        pytest.skip(f"Europe PMC unreachable: {exc}")

    assert documents, "expected at least one preprint for 'IRAK4 degrader'"

    for doc in documents:
        assert doc.source == "preprints"
        assert doc.doc_type == "preprint"
        assert doc.doc_id, "empty doc_id"
        assert doc.title.strip(), f"empty title for {doc.doc_id}"
        assert doc.url.startswith("https://"), f"bad url: {doc.url!r}"
        assert doc.raw.strip(), f"empty raw for {doc.doc_id}"

    # At least some preprints should carry an abstract in the indexed text.
    assert any("Abstract:" in doc.text for doc in documents), \
        "expected at least one preprint with abstract text"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-m", "network"]))
