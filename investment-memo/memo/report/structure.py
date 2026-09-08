"""The memo's section structure (declarative).

Each section names the analysis module that feeds it and whether it is required. The
assembler walks this list; the refusal path uses ``required`` to decide when the memo
cannot honestly be produced. Order is document order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    module: Optional[str]  # producing analysis module; None = synthesized/cross-module
    required: bool
    description: str


MEMO_SECTIONS: List[Section] = [
    Section("thesis", "Executive Summary & Thesis", None, True,
            "The recommendation and where our view departs from what the price implies. "
            "Synthesized across all modules; written last."),
    Section("overview", "Company & Pipeline Overview", None, True,
            "The company, its lead assets, stage, and cash position. Draws on filings and trials."),
    Section("moa", "Mechanism of Action", "moa", True,
            "Does the drug work: target, mechanism, and the clinical proof points."),
    Section("pos", "Probability of Success", "pos", True,
            "Evidence-grounded PoS from the rubric, not generic phase-transition rates."),
    Section("regulatory", "Regulatory Path", "regulatory", True,
            "Designations, endpoints, filing/PDUFA; absence reported rather than guessed."),
    Section("peak_sales", "Commercial Opportunity", "peak_sales", True,
            "Peak sales from the deterministic model; a risk-adjusted range with sensitivities."),
    Section("valuation", "Valuation & Price vs Thesis", "price", True,
            "rNPV and balance sheet vs the value the market price implies; where we depart."),
    Section("risks", "Risks", None, False,
            "The key risks to the thesis, drawn from filings risk factors and the analysis."),
    Section("catalysts", "Catalysts", None, False,
            "Upcoming readouts, filings, and decisions that would move the view."),
    Section("sources", "Sources", None, True,
            "The evidence appendix: every document cited across the memo."),
]


def section_by_id(section_id: str) -> Section:
    for section in MEMO_SECTIONS:
        if section.id == section_id:
            return section
    raise KeyError(f"unknown memo section: {section_id}")
