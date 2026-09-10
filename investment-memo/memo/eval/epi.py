"""Epidemiology-grounding evals for the peak-sales module.

Two evaluators, both aimed at the failure mode where the addressable population is wrong
(the PRAX bug: 4,000 "rare pediatric epilepsy" patients for a lead asset that treats
focal epilepsy, a market of hundreds of thousands):

- :class:`EpiRetrievalEvaluator` - deterministic, no model. Runs the peak-sales
  epidemiology retrieval queries and asks whether they surface prevalence/epidemiology
  evidence for the LEAD indication (indication terms and epi terms co-occurring in a
  retrieved chunk), plus recall against any gold doc ids. This measures the retrieval gap
  so it can be improved.
- :class:`EpiGroundingEvaluator` - inspects a produced :class:`AnalysisResult` (generated
  upstream with a REAL model, never mocked, per MEMORY). Checks that the
  ``epidemiology_population`` parameter is cited to an epidemiology source and that its
  magnitude falls in the plausible range for the lead indication.

Reference sets live under ``evals/epi/<COMPANY>.json`` and are version-controlled.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from memo.analysis.base import AnalysisResult, Claim, Evidence
from memo.analysis.peak_sales import (
    _QUERIES as PEAK_SALES_QUERIES,
    epidemiology_population_flags,
)
from memo.eval.base import Evaluator
from memo.eval.retrieval import _matches, _unique_in_order
from memo.eval.types import CaseResult, EvalReport, mean
from memo.rag.retriever import Retriever

DEFAULT_EPI_ROOT = Path("evals") / "epi"


@dataclass
class EpiReference:
    """Ground truth for one company's epidemiology grounding.

    ``indication_terms`` / ``epi_terms`` are lowercase keyword/entity lists used for the
    grounded presence check; ``plausible_population_min/max`` bound a sane addressable
    population for the lead indication; ``relevant_doc_ids`` (optional) are epidemiology
    doc ids that retrieval should surface (family-matched like the retrieval eval).
    """

    company: str
    lead_indication: str
    indication_terms: List[str]
    epi_terms: List[str]
    plausible_population_min: float
    plausible_population_max: float
    lead_asset: str = ""
    relevant_doc_ids: List[str] = field(default_factory=list)
    note: str = ""


def load_epi_reference(company: str, root: Path = DEFAULT_EPI_ROOT) -> EpiReference:
    """Load the epidemiology reference for a company, raising if absent."""
    path = Path(root) / f"{company}.json"
    if not path.exists():
        raise FileNotFoundError(f"no epi reference for {company} at {path}")
    payload = json.loads(path.read_text())
    return EpiReference(
        company=payload["company"],
        lead_indication=payload["lead_indication"],
        indication_terms=[t.lower() for t in payload.get("indication_terms", [])],
        epi_terms=[t.lower() for t in payload.get("epi_terms", [])],
        plausible_population_min=float(payload["plausible_population_min"]),
        plausible_population_max=float(payload["plausible_population_max"]),
        lead_asset=payload.get("lead_asset", ""),
        relevant_doc_ids=list(payload.get("relevant_doc_ids", [])),
        note=payload.get("note", ""),
    )


def _hits(text: str, terms: Sequence[str]) -> bool:
    """True if any term appears (case-insensitively) in ``text``."""
    lowered = text.lower()
    return any(term in lowered for term in terms)


class EpiRetrievalEvaluator(Evaluator):
    """Does the peak-sales epidemiology retrieval surface epi evidence for the lead indication?

    Deterministic: runs the module's own retrieval queries (so it grades real behavior) and
    scores, per query and aggregated, whether the top-k chunks mention the lead indication,
    mention epidemiology signal, and -- the one that matters -- carry BOTH in a single chunk
    (epidemiology for the lead indication). Optional gold doc ids add recall@k.
    """

    name = "epi-retrieval"

    def __init__(
        self,
        reference: EpiReference,
        retriever: Retriever,
        k: int = 8,
        queries: Optional[Sequence[str]] = None,
    ) -> None:
        self.reference = reference
        self.retriever = retriever
        self.k = k
        # Default to the peak-sales module's actual epidemiology-bearing queries, so the
        # eval tracks the retrieval the module really runs.
        self.queries = list(queries) if queries is not None else list(PEAK_SALES_QUERIES)

    def run(self) -> EvalReport:
        case_results = [
            self._score_query(i, q) for i, q in enumerate(self.queries, start=1)
        ]
        aggregate = self._aggregate(case_results)
        # Corpus-level: did ANY query surface epidemiology for the lead indication?
        aggregate["epi_for_indication_any"] = (
            1.0
            if any(c.metrics.get("epi_for_indication@k", 0.0) >= 1.0 for c in case_results)
            else 0.0
        )
        return EvalReport(
            name=self.name,
            company=self.reference.company,
            params={
                "k": self.k,
                "queries": len(self.queries),
                "lead_indication": self.reference.lead_indication,
                "gold_docs": len(self.reference.relevant_doc_ids),
            },
            case_results=case_results,
            aggregate=aggregate,
        )

    # ---------------------------------------------------------------- internals

    def _score_query(self, idx: int, query: str) -> CaseResult:
        results = self.retriever.retrieve(query, k=self.k, company=self.reference.company)
        chunks = [r.chunk for r in results]
        retrieved = _unique_in_order(c.doc_id for c in chunks)

        indication = any(_hits(c.text, self.reference.indication_terms) for c in chunks)
        epi = any(_hits(c.text, self.reference.epi_terms) for c in chunks)
        both = any(
            _hits(c.text, self.reference.indication_terms)
            and _hits(c.text, self.reference.epi_terms)
            for c in chunks
        )

        metrics = {
            "indication@k": 1.0 if indication else 0.0,
            "epi@k": 1.0 if epi else 0.0,
            "epi_for_indication@k": 1.0 if both else 0.0,
        }
        if self.reference.relevant_doc_ids:
            matched = {
                gold
                for gold in self.reference.relevant_doc_ids
                if any(_matches(gold, doc_id) for doc_id in retrieved)
            }
            metrics[f"recall@{self.k}"] = len(matched) / len(
                self.reference.relevant_doc_ids
            )

        return CaseResult(
            case_id=f"q{idx}",
            query=query,
            relevant=self.reference.relevant_doc_ids,
            retrieved=retrieved[:6],
            metrics=metrics,
        )

    def _aggregate(self, case_results: List[CaseResult]) -> dict:
        keys = {k for c in case_results for k in c.metrics}
        agg = {}
        for key in keys:
            vals = [c.metrics[key] for c in case_results if key in c.metrics]
            if vals:
                agg[key] = mean(vals)
        return agg


class EpiGroundingEvaluator(Evaluator):
    """Is the produced addressable population cited to epidemiology and plausibly sized?

    Inspects the ``epidemiology_population`` claim in a peak-sales :class:`AnalysisResult`
    (generated upstream with a real model). No model call of its own -- a deterministic
    audit of the produced parameter:

    - ``cited_epi_source``: the population cites a CDC/PubMed/Orphanet/preprint source.
    - ``plausible_magnitude``: it clears the module's plausibility floor (or is grounded on
      a rare-disease source).
    - ``in_expected_range``: it falls within the reference's plausible band for the lead
      indication -- the direct catch for the PRAX 4,000-patient failure.
    """

    name = "epi-grounding"

    def __init__(self, analysis: AnalysisResult, reference: EpiReference) -> None:
        self.analysis = analysis
        self.reference = reference

    def run(self) -> EvalReport:
        claim = self._population_claim()
        value = self._population_value(claim)
        evidence: List[Evidence] = list(claim.evidence) if claim else []

        cited_epi, plausible, _notes = epidemiology_population_flags(value, evidence)
        in_range = (
            self.reference.plausible_population_min
            <= value
            <= self.reference.plausible_population_max
        )

        detail = {
            "population": f"{value:,.0f}",
            "lead_indication": self.reference.lead_indication,
            "expected_range": (
                f"{self.reference.plausible_population_min:,.0f}-"
                f"{self.reference.plausible_population_max:,.0f}"
            ),
            "cited_sources": ", ".join(sorted({e.source for e in evidence})) or "(none)",
            "statement": claim.statement if claim else "(no epidemiology_population claim)",
        }

        case = CaseResult(
            case_id="epidemiology_population",
            query=f"addressable population for {self.reference.lead_indication}",
            relevant=[],
            retrieved=[e.doc_id for e in evidence],
            metrics={
                "cited_epi_source": 1.0 if cited_epi else 0.0,
                "plausible_magnitude": 1.0 if plausible else 0.0,
                "in_expected_range": 1.0 if in_range else 0.0,
            },
            detail=detail,
        )
        return EvalReport(
            name=self.name,
            company=self.analysis.company,
            params={
                "module": self.analysis.module,
                "lead_indication": self.reference.lead_indication,
                "population": detail["population"],
            },
            case_results=[case],
            aggregate=dict(case.metrics),
        )

    # ---------------------------------------------------------------- internals

    def _population_claim(self) -> Optional[Claim]:
        for claim in self.analysis.claims:
            if claim.statement.strip().startswith("epidemiology_population"):
                return claim
        return None

    @staticmethod
    def _population_value(claim: Optional[Claim]) -> float:
        """Parse the numeric population from a claim's value or statement (0.0 if absent)."""
        if claim is None:
            return 0.0
        for source in (claim.value, claim.statement):
            if not source:
                continue
            match = re.search(r"[-+]?\d[\d,]*\.?\d*", source)
            if match:
                try:
                    return float(match.group(0).replace(",", ""))
                except ValueError:
                    continue
        return 0.0
