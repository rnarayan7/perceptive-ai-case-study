"""Retrieval evaluation.

Deterministic, ground-truth eval: for each gold query, retrieve top-k and score how
well the relevant documents are returned. Needs no model, which is why it is first.
Reports hit@k, recall@k, precision@k, and MRR, per case and aggregated.
"""

from __future__ import annotations

from typing import List

from memo.eval.base import Evaluator
from memo.eval.goldset import GoldSet
from memo.eval.types import CaseResult, EvalReport, mean
from memo.rag.retriever import Retriever


class RetrievalEvaluator(Evaluator):
    name = "retrieval"

    def __init__(self, goldset: GoldSet, retriever: Retriever, k: int = 8,
                 abstain_threshold: float = 15.0) -> None:
        self.goldset = goldset
        self.retriever = retriever
        self.k = k
        # Hard negatives are scored "abstained" when the top BM25 score is below this.
        # It is deliberately coarse: present vs absent BM25 scores overlap (~15), so this
        # measures gross false positives, not calibrated abstention (see follow-ups).
        self.abstain_threshold = abstain_threshold

    def run(self) -> EvalReport:
        case_results = [self._score_case(case) for case in self.goldset.cases]
        n_neg = sum(1 for c in self.goldset.cases if c.negative)
        return EvalReport(
            name=self.name,
            company=self.goldset.company,
            params={"k": self.k, "cases": len(self.goldset.cases),
                    "negatives": n_neg, "abstain_threshold": self.abstain_threshold},
            case_results=case_results,
            aggregate=self._aggregate(case_results),
        )

    # ---------------------------------------------------------------- internals

    def _score_case(self, case) -> CaseResult:
        results = self.retriever.retrieve(
            case.query, k=self.k, company=self.goldset.company
        )
        retrieved = _unique_in_order(r.chunk.doc_id for r in results)
        top_score = results[0].score if results else 0.0

        # Hard negative: the answer is not in the corpus, so the retriever should signal
        # nothing relevant. Scored on top-score abstention, separate from hit metrics.
        if case.negative:
            abstained = 1.0 if top_score < self.abstain_threshold else 0.0
            return CaseResult(
                case_id=case.id, query=case.query, relevant=[], retrieved=retrieved[:3],
                metrics={"abstained": abstained},
                detail={"top_score": f"{top_score:.2f}", "type": "hard-negative"},
            )

        relevant = case.relevant_doc_ids

        # A gold id is "found" when any retrieved id belongs to its doc family
        # (see ``_matches``). Count it once per gold id, however many sections of
        # that family were retrieved, so segmentation does not inflate metrics.
        matched_gold = {
            gold for gold in relevant
            if any(_matches(gold, doc_id) for doc_id in retrieved)
        }
        n_matched = len(matched_gold)
        recall = n_matched / len(relevant) if relevant else 0.0
        precision = n_matched / len(retrieved) if retrieved else 0.0
        mrr = 0.0
        for rank, doc_id in enumerate(retrieved, start=1):
            if any(_matches(gold, doc_id) for gold in relevant):
                mrr = 1.0 / rank
                break

        # hit@1 and hit@3 add headroom: hit@k alone saturates at 1.0 because the right
        # doc almost always lands somewhere in the top k. hit@1 (ranked first) is the
        # discriminating metric a reranker/embeddings change would move.
        metrics = {f"hit@{n}": _hit_at(retrieved, relevant, n)
                   for n in sorted({1, 3, self.k})}
        metrics[f"recall@{self.k}"] = recall
        metrics[f"precision@{self.k}"] = precision
        metrics["mrr"] = mrr

        return CaseResult(
            case_id=case.id,
            query=case.query,
            relevant=case.relevant_doc_ids,
            retrieved=retrieved,
            metrics=metrics,
        )

    def _aggregate(self, case_results: List[CaseResult]) -> dict:
        """Average each metric over the cases that have it.

        Positive and negative cases carry different metric keys (hit@/recall vs
        abstained), so average each key only over the cases where it appears rather than
        assuming every case shares one key set.
        """
        keys = {k for c in case_results for k in c.metrics}
        agg = {}
        for key in keys:
            vals = [c.metrics[key] for c in case_results if key in c.metrics]
            if vals:
                agg[key] = mean(vals)
        return agg


def _hit_at(retrieved: List[str], relevant: List[str], n: int) -> float:
    """1.0 if any relevant doc-family appears within the top-n retrieved ids."""
    return 1.0 if any(_matches(g, d) for d in retrieved[:n] for g in relevant) else 0.0


def _matches(gold_id: str, retrieved_id: str) -> bool:
    """True if a retrieved doc_id belongs to the family named by a gold id.

    A gold id matches when the retrieved id equals it exactly, or when the
    retrieved id is a section of it: the retrieved id starts with
    ``gold_id + "-"``. This lets a gold set reference the STABLE base id (a bare
    EDGAR accession, an NCT id) and keep matching after re-ingestion segments a
    filing into sectioned ids like ``000119312526333849-pii-item1a``.

    Matching by family (not exact set membership) means gold sets survive
    changes to the doc_id scheme. A gold id that is itself a full section id
    still matches that exact id. The trailing ``-`` guard keeps a sibling
    accession like ``000119312526333849X`` from matching the base ``...849``.
    """
    return retrieved_id == gold_id or retrieved_id.startswith(gold_id + "-")


def _unique_in_order(items) -> List[str]:
    seen = set()
    ordered = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered
