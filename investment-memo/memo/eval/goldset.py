"""Gold sets: curated evaluation cases, committed to the repo.

A gold set is the ground truth an evaluator scores against. Retrieval gold sets map
an analyst-style query to the document ids that should be retrieved for it. They live
under ``evals/<eval_type>/<COMPANY>.json`` and are version-controlled (unlike ``data/``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

DEFAULT_GOLD_ROOT = Path("evals")


@dataclass
class GoldCase:
    id: str
    query: str
    relevant_doc_ids: List[str]
    note: str = ""
    negative: bool = False  # hard negative: answer is not in the corpus; expect abstention


@dataclass
class GoldSet:
    company: str
    eval_type: str
    cases: List[GoldCase] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cases)


def load_goldset(path: Path) -> GoldSet:
    """Load a gold set from a JSON file."""
    payload = json.loads(Path(path).read_text())
    cases = [
        GoldCase(
            id=c["id"],
            query=c["query"],
            relevant_doc_ids=list(c["relevant_doc_ids"]),
            note=c.get("note", ""),
            negative=bool(c.get("negative", False)),
        )
        for c in payload.get("cases", [])
    ]
    return GoldSet(company=payload["company"], eval_type=payload["eval_type"], cases=cases)


def goldset_path(eval_type: str, company: str, root: Path = DEFAULT_GOLD_ROOT) -> Path:
    return Path(root) / eval_type / f"{company}.json"


def load_goldset_for(eval_type: str, company: str, root: Path = DEFAULT_GOLD_ROOT) -> GoldSet:
    """Load the gold set for an (eval_type, company) pair, raising if absent."""
    path = goldset_path(eval_type, company, root)
    if not path.exists():
        raise FileNotFoundError(f"no {eval_type} gold set for {company} at {path}")
    return load_goldset(path)
