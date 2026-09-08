"""Offline tests for the eval harness (deterministic, no network, no models)."""

from __future__ import annotations

from memo.eval import RetrievalEvaluator
from memo.eval.goldset import GoldCase, GoldSet
from memo.ingestion.base import Document, Storage
from memo.rag import build_retriever


def _doc(doc_id, text, source="clinicaltrials", doc_type="study"):
    return Document(
        company="TEST", source=source, doc_type=doc_type, doc_id=doc_id,
        title=doc_id, url=f"https://example.com/{doc_id}", text=text,
    )


def _retriever(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(_doc("NCT001", "KT-474 single ascending dose healthy volunteers"))
    storage.write_document(_doc("NCT002", "KT-333 refractory lymphoma clinical activity"))
    storage.write_document(_doc("NCT003", "KT-621 asthma long term oral study"))
    return build_retriever("TEST", storage=storage)


def test_retrieval_eval_perfect_case(tmp_path):
    goldset = GoldSet("TEST", "retrieval", [
        GoldCase("c1", "KT-474 ascending dose healthy volunteers", ["NCT001"]),
    ])
    report = RetrievalEvaluator(goldset, _retriever(tmp_path), k=3).run()
    m = report.case_results[0].metrics
    assert m["hit@3"] == 1.0
    assert m["recall@3"] == 1.0
    assert m["mrr"] == 1.0  # relevant doc ranked first


def test_retrieval_eval_miss_case(tmp_path):
    goldset = GoldSet("TEST", "retrieval", [
        GoldCase("c1", "KT-474 ascending dose", ["NCT999"]),  # not in corpus
    ])
    report = RetrievalEvaluator(goldset, _retriever(tmp_path), k=3).run()
    m = report.case_results[0].metrics
    assert m["hit@3"] == 0.0
    assert m["recall@3"] == 0.0
    assert m["mrr"] == 0.0


def test_matches_exact_family_and_non_match():
    from memo.eval.retrieval import _matches

    base = "000119312526333849"
    # Exact match.
    assert _matches(base, base)
    # Section-suffix family match (the doc_id scheme after segmentation).
    assert _matches(base, f"{base}-pi-item1")
    assert _matches(base, f"{base}-pii-item1a")
    # A gold id that is itself a full section id still matches exactly.
    assert _matches(f"{base}-pi-item1", f"{base}-pi-item1")
    # Sibling accession must NOT match the base (trailing '-' guard).
    assert not _matches(base, f"{base}X")
    assert not _matches(base, f"{base}0")
    # A section id gold does not match the bare base or a different section.
    assert not _matches(f"{base}-pi-item1", base)
    assert not _matches(f"{base}-pi-item1", f"{base}-pii-item1a")


def test_retrieval_eval_family_match_base_id_hits_sectioned_doc(tmp_path):
    # Gold references the stable base accession; the ingested doc is a section id.
    storage = Storage(root=tmp_path)
    storage.write_document(
        _doc("000119312526333849-pi-item1",
             "cash and cash equivalents on the balance sheet",
             source="edgar", doc_type="10-Q")
    )
    retriever = build_retriever("TEST", storage=storage)
    goldset = GoldSet("TEST", "retrieval", [
        GoldCase("cash", "cash and cash equivalents quarterly report",
                 ["000119312526333849"]),
    ])
    report = RetrievalEvaluator(goldset, retriever, k=8).run()
    m = report.case_results[0].metrics
    assert m["hit@8"] == 1.0
    assert m["recall@8"] == 1.0
    assert m["mrr"] == 1.0


def test_retrieval_hard_negative_abstention_and_mixed_aggregate(tmp_path):
    from memo.eval import RetrievalEvaluator
    from memo.eval.goldset import GoldCase, GoldSet
    from memo.ingestion.base import Document, Storage
    from memo.rag import build_retriever

    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="clinicaltrials", doc_type="study", doc_id="D1",
        title="KT-621 asthma", url="u", text="KT-621 oral degrader asthma clinical trial"))
    retr = build_retriever("TEST", storage=storage)

    neg = GoldSet("TEST", "retrieval", [
        GoldCase("neg", "GLP-1 obesity weight loss", [], negative=True)])
    # High threshold: any top score is "below", so the out-of-scope query abstains (correct).
    assert RetrievalEvaluator(neg, retr, abstain_threshold=100.0).run().aggregate["abstained"] == 1.0
    # Zero threshold: nothing is below it, so it never abstains (false positive).
    assert RetrievalEvaluator(neg, retr, abstain_threshold=0.0).run().aggregate["abstained"] == 0.0

    mixed = GoldSet("TEST", "retrieval", [
        GoldCase("pos", "KT-621 asthma", ["D1"]),
        GoldCase("neg", "GLP-1 obesity", [], negative=True)])
    agg = RetrievalEvaluator(mixed, retr, abstain_threshold=100.0).run().aggregate
    assert "hit@1" in agg and "abstained" in agg  # mixed keys aggregate cleanly


def test_faithfulness_verdict_metrics():
    from memo.eval import verdict_metrics

    assert verdict_metrics("supported") == {"supported": 1.0, "contradicted": 0.0, "faithfulness": 1.0}
    assert verdict_metrics("partial")["faithfulness"] == 0.5
    assert verdict_metrics("unsupported")["faithfulness"] == 0.0
    assert verdict_metrics("contradicted") == {"supported": 0.0, "contradicted": 1.0, "faithfulness": 0.0}


def test_faithfulness_ungrounded_claim_is_unsupported_without_a_call():
    # An evidence-free claim must be graded unsupported without ever calling the judge,
    # so a judge that would explode proves no call happened.
    from memo.analysis.base import AnalysisResult, Claim
    from memo.eval import FaithfulnessEvaluator

    class ExplodingJudge:
        def complete_json(self, *a, **k):
            raise AssertionError("judge should not be called for an evidence-free claim")

    analysis = AnalysisResult(
        company="TEST", module="moa", summary="s",
        claims=[Claim(statement="ungrounded", confidence=0.9, rationale="", evidence=[])],
    )
    report = FaithfulnessEvaluator(analysis, ExplodingJudge()).run()
    assert report.case_results[0].detail["verdict"] == "unsupported"
    assert report.aggregate["faithfulness"] == 0.0


def test_retrieval_eval_aggregates(tmp_path):
    goldset = GoldSet("TEST", "retrieval", [
        GoldCase("c1", "KT-474 ascending dose healthy", ["NCT001"]),
        GoldCase("c2", "KT-333 lymphoma", ["NCT002"]),
    ])
    report = RetrievalEvaluator(goldset, _retriever(tmp_path), k=3).run()
    assert set(report.aggregate) == {"hit@1", "hit@3", "recall@3", "precision@3", "mrr"}
    assert 0.0 <= report.aggregate["recall@3"] <= 1.0
    assert report.params["cases"] == 2
