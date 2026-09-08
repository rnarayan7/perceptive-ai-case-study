"""Analysis module interface and shared output types.

Every analytical question (MoA, PoS, regulatory, peak sales, price) is an
:class:`AnalysisModule` that turns retrieved evidence into typed :class:`Claim` objects.
A claim always carries the evidence it rests on, which is what makes the memo auditable
and what the faithfulness eval will grade. This mirrors the ingestion and eval packages:
one interface, a registry, typed outputs.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import List, Optional

from memo.analysis.context import AnalysisContext


@dataclass
class Evidence:
    """A citation back to a specific piece of retrieved source material."""

    doc_id: str
    source: str
    doc_type: str
    url: str
    quote: str  # the span the claim rests on
    date: Optional[str] = None
    chunk_id: Optional[str] = None


@dataclass
class Claim:
    """One grounded assertion produced by an analysis module."""

    statement: str
    confidence: float  # 0..1
    rationale: str
    evidence: List[Evidence] = field(default_factory=list)
    value: Optional[str] = None  # an estimate/number where the claim carries one

    @property
    def is_grounded(self) -> bool:
        """A claim with no evidence is unsupported by construction."""
        return len(self.evidence) > 0


@dataclass
class AnalysisResult:
    """The output of running one analysis module for one company."""

    company: str
    module: str
    summary: str
    claims: List[Claim] = field(default_factory=list)
    confidence: float = 0.0
    notes: List[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)  # token usage, for cost reporting

    @property
    def grounded_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.is_grounded]


class AnalysisModule(abc.ABC):
    """Base class for all analysis modules."""

    #: Short module name, e.g. "moa". Set by subclasses.
    name: str = ""

    @abc.abstractmethod
    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        """Produce an analysis for the company described by ``context``."""
        raise NotImplementedError
