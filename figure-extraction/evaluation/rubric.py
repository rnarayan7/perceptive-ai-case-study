"""Rubric scoring for the answers that are not numbers.

Three of the four spider quantities are reasoning, not measurement: the rule used
to turn each trajectory into an event or a censoring time, and the range of
medians the figure cannot resolve. They were marked interpretive and excluded
from scoring entirely, which meant most of the work on that figure was invisible
to the metric and the whole figure hung on one landmark value.

They are checkable, though, because the brief says exactly what each answer has
to contain. So each is scored against explicit binary criteria taken from the
brief's own wording rather than from our opinion of a good answer. A model judges
whether the text satisfies each criterion, which is a language task and the one
place a judge belongs; it never assigns a number or grades a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "description": "One verdict per criterion, in the order given.",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "met": {"type": "boolean"},
                    "evidence": {
                        "type": "string",
                        "description": "The phrase from the answer that satisfies it, "
                                       "or why it does not.",
                    },
                },
                "required": ["criterion", "met", "evidence"],
            },
        },
    },
    "required": ["criteria"],
}

_PROMPT = """You are checking whether an answer contains specific required content.

The answer is to this question:
{question}

The answer given was:
{answer}

For each criterion below, decide whether the answer satisfies it. Judge only
whether the required content is present and correct; do not reward style, length
or confidence. Quote the phrase that satisfies each criterion, or say what is
missing.

Criteria:
{criteria}
"""


@dataclass
class RubricResult:
    """How much of a required answer was actually present."""

    key: str
    met: List[str] = field(default_factory=list)
    missed: List[str] = field(default_factory=list)
    evidence: Dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> float:
        total = len(self.met) + len(self.missed)
        return (len(self.met) / total) if total else 0.0

    def __str__(self) -> str:
        return (f"{self.key}: {len(self.met)}/{len(self.met) + len(self.missed)} criteria met"
                + (f"  missing: {'; '.join(self.missed)}" if self.missed else ""))


def score_rubric(key: str, question: str, answer: str,
                 criteria: Sequence[str], client) -> Optional[RubricResult]:
    """Score one free-text answer against explicit criteria."""
    if not answer or not criteria or not hasattr(client, "complete_json"):
        return None
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))
    js = client.complete_json(
        _PROMPT.format(question=question, answer=answer, criteria=numbered),
        RUBRIC_SCHEMA, max_tokens=1200, tool_name="report_rubric")
    if not js:
        return None
    res = RubricResult(key=key)
    reported = js.get("criteria") or []
    for i, c in enumerate(criteria):
        entry = reported[i] if i < len(reported) and isinstance(reported[i], dict) else {}
        if bool(entry.get("met")):
            res.met.append(c)
        else:
            res.missed.append(c)
        res.evidence[c] = str(entry.get("evidence", ""))
    return res
