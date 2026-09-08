"""Deterministic extraction / factuality evaluation with auto-derived gold.

This evaluator checks that the authoritative factual values the system can surface
(trial enrollment / phase / status / primary-completion date, filing form / date, ...)
match the authoritative source. There is NO model in the scorer: strings and dates are
compared with exact match, counts with a numeric tolerance.

The gold is *auto-derived* from the structured fields already ingested. Those fields
ARE the authoritative source (``StructuredStore.trials()`` / ``.filings()`` read back
what ingestion wrote from ClinicalTrials.gov and EDGAR), so ``derive_gold`` reduces them
to objective, unambiguous facts and writes one committed gold file per company under
``evals/extraction/<COMPANY>.json`` for later human spot-check.

Resolving a case *today* means reading the same value back through ``StructuredStore``
and comparing it to the derived ``expected`` -- so run against the ingested corpus it is
a self-check (it verifies the derivation round-trips and that no record drifted). The
lasting value of the harness is the gold contract: when a memo section or another module
later *reports* one of these facts, the exact same gold checks its numbers against the
authoritative source without any model in the loop. The gold files are DRAFTS pending
human spot-check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from memo.eval.base import Evaluator
from memo.eval.goldset import DEFAULT_GOLD_ROOT, goldset_path
from memo.eval.types import CaseResult, EvalReport, mean
from memo.ingestion.base import Storage
from memo.rag.structured import StructuredStore

EVAL_TYPE = "extraction"

#: Marker recorded in every derived gold file. These are machine-derived candidates.
DRAFT_NOTE = (
    "DRAFT gold, auto-derived from ingested structured fields (the authoritative "
    "source); pending human spot-check. Each case resolves a value through "
    "StructuredStore and compares it to `expected` with no model in the scorer."
)


# --------------------------------------------------------------------------- gold


@dataclass
class ExtractionCase:
    """One objective fact to check.

    ``locator`` says how to resolve the value from the system, e.g.
    ``{"kind": "trial", "nct_id": "NCT...", "attribute": "enrollment"}`` or
    ``{"kind": "filing", "accession": "0001...", "attribute": "form"}``.
    ``tolerance`` (when set) marks the case numeric: it passes when
    ``abs(actual - expected) <= tolerance``. Absent tolerance means an exact
    string/date match.
    """

    id: str
    question: str
    field: str
    expected: Any
    locator: Dict[str, Any]
    tolerance: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "question": self.question,
            "field": self.field,
            "expected": self.expected,
            "locator": self.locator,
        }
        if self.tolerance is not None:
            d["tolerance"] = self.tolerance
        return d


@dataclass
class ExtractionGoldSet:
    company: str
    eval_type: str = EVAL_TYPE
    cases: List[ExtractionCase] = field(default_factory=list)
    note: str = DRAFT_NOTE

    def __len__(self) -> int:
        return len(self.cases)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company": self.company,
            "eval_type": self.eval_type,
            "note": self.note,
            "cases": [c.to_dict() for c in self.cases],
        }


def load_extraction_gold(path: Path) -> ExtractionGoldSet:
    """Load a derived extraction gold set from JSON."""
    payload = json.loads(Path(path).read_text())
    cases = [
        ExtractionCase(
            id=c["id"],
            question=c["question"],
            field=c["field"],
            expected=c["expected"],
            locator=dict(c["locator"]),
            tolerance=c.get("tolerance"),
        )
        for c in payload.get("cases", [])
    ]
    return ExtractionGoldSet(
        company=payload["company"],
        eval_type=payload.get("eval_type", EVAL_TYPE),
        cases=cases,
        note=payload.get("note", DRAFT_NOTE),
    )


# --------------------------------------------------------------------- derivation

# Trial attributes reduced to gold. Each entry: (attribute, field label, human phrasing,
# tolerance). tolerance=None => exact string/date match; a number => numeric compare.
_TRIAL_FIELDS = [
    ("enrollment", "trial.enrollment", "enrollment count", 0.0),
    ("phase", "trial.phase", "trial phase", None),
    ("overall_status", "trial.overall_status", "overall status", None),
    ("primary_completion_date", "trial.primary_completion_date", "primary completion date", None),
]

# Filing attributes reduced to gold.
_FILING_FIELDS = [
    ("form", "filing.form", "filing form type", None),
    ("filing_date", "filing.filing_date", "filing date", None),
]


def derive_gold(
    company: str,
    storage: Optional[Storage] = None,
    root: Path = DEFAULT_GOLD_ROOT,
    write: bool = True,
) -> ExtractionGoldSet:
    """Derive extraction gold from a company's ingested structured fields and commit it.

    Emits cases only for fields that are unambiguous and present, so an absent value
    never produces a case that would spuriously fail. Filings are segmented per SEC Item
    on disk (many docs share one accession), so filing cases are de-duplicated to one per
    accession -- every segment of a given filing agrees on form and filing date.

    Writes ``evals/extraction/<COMPANY>.json`` when ``write`` is True and returns the set.
    """
    store = StructuredStore(company, storage)
    cases: List[ExtractionCase] = []

    for trial in store.trials():
        for attribute, label, phrasing, tol in _TRIAL_FIELDS:
            expected = _trial_value(trial, attribute)
            if not _has_value(expected):
                continue
            cases.append(
                ExtractionCase(
                    id=f"{trial.nct_id}-{attribute}",
                    question=f"What is the {phrasing} for trial {trial.nct_id}?",
                    field=label,
                    expected=expected,
                    locator={"kind": "trial", "nct_id": trial.nct_id, "attribute": attribute},
                    tolerance=tol,
                )
            )

    seen_accessions: set = set()
    for filing in store.filings():
        if filing.accession in seen_accessions:
            continue
        seen_accessions.add(filing.accession)
        for attribute, label, phrasing, tol in _FILING_FIELDS:
            expected = _filing_value(filing, attribute)
            if not _has_value(expected):
                continue
            cases.append(
                ExtractionCase(
                    id=f"{filing.accession}-{attribute}",
                    question=f"What is the {phrasing} for filing {filing.accession}?",
                    field=label,
                    expected=expected,
                    locator={"kind": "filing", "accession": filing.accession, "attribute": attribute},
                    tolerance=tol,
                )
            )

    goldset = ExtractionGoldSet(company=company, cases=cases)
    if write:
        path = goldset_path(EVAL_TYPE, company, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(goldset.to_dict(), indent=2) + "\n")
    return goldset


# --------------------------------------------------------------------- evaluator


class ExtractionEvaluator(Evaluator):
    """Score derived gold by resolving each value through ``StructuredStore``.

    Deterministic: exact match for strings/dates, ``abs(actual - expected) <= tolerance``
    for numeric cases. No model call.
    """

    name = EVAL_TYPE

    def __init__(self, goldset: ExtractionGoldSet, store: StructuredStore) -> None:
        self.goldset = goldset
        self.store = store

    def run(self) -> EvalReport:
        case_results = [self._score_case(case) for case in self.goldset.cases]
        aggregate = self._aggregate(case_results)
        return EvalReport(
            name=self.name,
            company=self.goldset.company,
            params={"cases": len(self.goldset.cases)},
            case_results=case_results,
            aggregate=aggregate,
        )

    # ------------------------------------------------------------ internals

    def _score_case(self, case: ExtractionCase) -> CaseResult:
        actual = self._resolve(case.locator)
        exact = 1.0 if self._matches(case.expected, actual, case.tolerance) else 0.0
        detail = {
            "field": case.field,
            "expected": _as_text(case.expected),
            "actual": _as_text(actual),
            "locator": json.dumps(case.locator, sort_keys=True),
        }
        if case.tolerance is not None:
            detail["tolerance"] = str(case.tolerance)
        return CaseResult(
            case_id=case.id,
            query=case.question,
            relevant=[_as_text(case.expected)],
            retrieved=[_as_text(actual)],
            metrics={"exact_match": exact},
            detail=detail,
        )

    def _resolve(self, locator: Dict[str, Any]) -> Any:
        """Read the value the locator points at from the structured store."""
        kind = locator.get("kind")
        attribute = locator.get("attribute")
        if kind == "trial":
            for trial in self.store.trials():
                if trial.nct_id == locator.get("nct_id"):
                    return _trial_value(trial, attribute)
            return None
        if kind == "filing":
            for filing in self.store.filings():
                if filing.accession == locator.get("accession"):
                    return _filing_value(filing, attribute)
            return None
        return None

    @staticmethod
    def _matches(expected: Any, actual: Any, tolerance: Optional[float]) -> bool:
        if actual is None:
            return False
        if tolerance is not None:
            try:
                return abs(float(actual) - float(expected)) <= float(tolerance)
            except (TypeError, ValueError):
                return False
        return _as_text(expected) == _as_text(actual)

    def _aggregate(self, case_results: List[CaseResult]) -> Dict[str, float]:
        if not case_results:
            return {"exact_match": 0.0, "cases": 0.0, "passed": 0.0, "failed": 0.0}
        scores = [c.metrics["exact_match"] for c in case_results]
        passed = sum(scores)
        return {
            "exact_match": mean(scores),
            "cases": float(len(case_results)),
            "passed": passed,
            "failed": float(len(case_results)) - passed,
        }


# --------------------------------------------------------------------- helpers


def _trial_value(trial, attribute: str) -> Any:
    if attribute == "enrollment":
        return trial.enrollment
    if attribute == "phase":
        return "|".join(trial.phases) if trial.phases else None
    if attribute == "overall_status":
        return trial.status
    if attribute == "primary_completion_date":
        return trial.primary_completion_date
    return None


def _filing_value(filing, attribute: str) -> Any:
    if attribute == "form":
        return filing.form
    if attribute == "filing_date":
        return filing.filing_date
    return None


def _has_value(value: Any) -> bool:
    """A field worth a gold case: present and non-empty."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def _as_text(value: Any) -> str:
    """Canonical string form used for exact comparison and detail reporting."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
