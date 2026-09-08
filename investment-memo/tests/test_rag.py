"""Offline tests for the RAG layer (no network)."""

from __future__ import annotations

from memo.ingestion.base import Document, Storage
from memo.rag import BM25Index, Chunker, StructuredStore, build_retriever, tokenize


def _doc(doc_id, text, source="edgar", doc_type="10-K", metadata=None):
    return Document(
        company="TEST",
        source=source,
        doc_type=doc_type,
        doc_id=doc_id,
        title=f"{doc_type} {doc_id}",
        url=f"https://example.com/{doc_id}",
        metadata=metadata or {},
        text=text,
    )


def test_tokenize_keeps_ids_and_digits():
    assert tokenize("Patient in NCT04772885 had 154 enrolled") == [
        "patient", "in", "nct04772885", "had", "154", "enrolled",
    ]


def test_tokenize_preserves_drug_codes():
    tokens = tokenize("KT-333 and IMVT-1402 dosing")
    # normal split still present, plus the de-hyphenated codes as distinctive tokens
    assert "kt333" in tokens
    assert "imvt1402" in tokens
    # a plain hyphenated phrase is not treated as a code
    assert tokenize("state-of-the-art assay") == ["state", "of", "the", "art", "assay"]


def test_bm25_ranks_relevant_doc_first():
    index = BM25Index().build(
        [
            "progression free survival was the primary endpoint",
            "the company reported cash and cash equivalents",
            "manufacturing and supply chain considerations",
        ]
    )
    hits = index.search("primary endpoint survival", k=3)
    assert hits, "expected at least one hit"
    assert hits[0][0] == 0


def test_chunker_overlap_and_ids():
    long_text = " ".join(f"word{i}" for i in range(700))
    chunks = Chunker(chunk_words=300, overlap_words=50).chunk_document(_doc("D1", long_text))
    assert len(chunks) >= 3
    assert chunks[0].chunk_id == "D1::0"
    # Overlap: the tail of chunk 0 should reappear at the head of chunk 1.
    assert chunks[0].text.split()[-1] in set(chunks[1].text.split())


def test_chunker_trims_xbrl_preamble():
    text = "us-gaap:Cash context tag\ndei:EntityName tag\nReal prose begins here about the trial."
    chunks = Chunker(chunk_words=50, overlap_words=5).chunk_document(_doc("X1", text))
    assert chunks
    assert "us-gaap" not in chunks[0].text
    assert "Real prose begins here" in chunks[0].text


def test_retriever_filters_by_source(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(_doc("E1", "cash and cash equivalents were reported", source="edgar"))
    storage.write_document(
        _doc("C1", "progression free survival primary endpoint", source="clinicaltrials", doc_type="study")
    )
    retriever = build_retriever("TEST", storage=storage)

    edgar_only = retriever.retrieve("cash equivalents", company="TEST", source="edgar")
    assert edgar_only and all(r.chunk.source == "edgar" for r in edgar_only)

    ct_only = retriever.retrieve("survival endpoint", company="TEST", source="clinicaltrials")
    assert ct_only and all(r.chunk.source == "clinicaltrials" for r in ct_only)


def test_retriever_filters_by_date(tmp_path):
    storage = Storage(root=tmp_path)
    old = _doc("OLD", "cash runway guidance and clinical update", source="edgar", doc_type="10-Q")
    old.published = "2024-05-01"
    new = _doc("NEW", "cash runway guidance and clinical update", source="edgar", doc_type="10-Q")
    new.published = "2026-05-01"
    storage.write_document(old)
    storage.write_document(new)
    retriever = build_retriever("TEST", storage=storage)

    recent = retriever.retrieve("cash runway guidance", company="TEST", since="2025-01-01")
    assert {r.chunk.doc_id for r in recent} == {"NEW"}

    both = retriever.retrieve("cash runway guidance", company="TEST")
    assert {r.chunk.doc_id for r in both} == {"OLD", "NEW"}

    window = retriever.retrieve(
        "cash runway guidance", company="TEST", since="2024-01-01", until="2024-12-31"
    )
    assert {r.chunk.doc_id for r in window} == {"OLD"}


def test_document_normalizes_bare_year_published():
    from memo.ingestion.base import Document, normalize_date

    assert normalize_date("2024") == "2024-01-01"
    assert normalize_date("2019-2020") == "2019-01-01"
    assert normalize_date("2024-08") == "2024-08-01"
    assert normalize_date("2024-08-05") == "2024-08-05"
    assert normalize_date(None) is None
    assert normalize_date("") is None
    d = Document(company="T", source="pubmed", doc_type="article", doc_id="1",
                 title="t", url="u", published="2024")
    assert d.published == "2024-01-01"  # normalized at construction


def test_retriever_date_filter_handles_bare_year(tmp_path):
    # Regression: a bare-year doc must NOT be wrongly excluded by a since-bound
    # inside its own year ("2024" < "2024-01-01" used to drop it).
    storage = Storage(root=tmp_path)
    doc = _doc("Y", "annual cash guidance update", source="pubmed", doc_type="article")
    doc.published = "2024"  # stored bare; normalized on reload via from_record
    storage.write_document(doc)
    retriever = build_retriever("TEST", storage=storage)

    hits = retriever.retrieve("cash guidance", company="TEST", since="2024-01-01")
    assert {r.chunk.doc_id for r in hits} == {"Y"}


def test_load_documents_honors_manifest_snapshot(tmp_path):
    from memo.ingestion.base import IngestManifest, utc_now_iso

    storage = Storage(root=tmp_path)
    keep = _doc("KEEP", "kept document text", source="clinicaltrials", doc_type="study")
    orphan = _doc("ORPHAN", "stale orphan text", source="clinicaltrials", doc_type="study")
    storage.write_document(keep)
    storage.write_document(orphan)  # on disk but not in the latest manifest

    manifest = IngestManifest(company="TEST", source="clinicaltrials", started_at=utc_now_iso())
    manifest.add(keep)
    storage.write_manifest(manifest)

    loaded = {d.doc_id for d in storage.load_documents("TEST", source="clinicaltrials")}
    assert loaded == {"KEEP"}  # orphan ignored: reads correspond to the latest run


def test_structured_store_reads_trials(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(
        _doc(
            "NCT04772885",
            "summary text",
            source="clinicaltrials",
            doc_type="study",
            metadata={
                "phases": ["PHASE1"],
                "overall_status": "COMPLETED",
                "enrollment": 154,
                "lead_sponsor": "Kymera Therapeutics, Inc.",
                "conditions": ["Atopic Dermatitis"],
                "interventions": [{"name": "KT-474", "type": "DRUG"}],
            },
        )
    )
    trials = StructuredStore("TEST", storage=storage).trials()
    assert len(trials) == 1
    trial = trials[0]
    assert trial.nct_id == "NCT04772885"
    assert trial.phases == ["PHASE1"]
    assert trial.enrollment == 154
    assert trial.interventions == ["KT-474 (DRUG)"]
