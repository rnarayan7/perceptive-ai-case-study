"""Result types shared across evaluators.

Kept generic so later evaluators (faithfulness, analytical quality) reuse the same
report shape as the retrieval evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class CaseResult:
    """Outcome of one evaluation case."""

    case_id: str
    query: str
    relevant: List[str]
    retrieved: List[str]  # doc ids in rank order
    metrics: Dict[str, float] = field(default_factory=dict)
    detail: Dict[str, str] = field(default_factory=dict)  # evaluator-specific, e.g. verdict/rationale


@dataclass
class EvalReport:
    """Per-case results plus aggregate metrics for one evaluator run."""

    name: str
    company: str
    params: Dict[str, object]
    case_results: List[CaseResult]
    aggregate: Dict[str, float]

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "company": self.company,
            "params": self.params,
            "aggregate": self.aggregate,
            "cases": [
                {
                    "case_id": c.case_id,
                    "query": c.query,
                    "relevant": c.relevant,
                    "retrieved": c.retrieved,
                    "metrics": c.metrics,
                    "detail": c.detail,
                }
                for c in self.case_results
            ],
        }


def mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0
