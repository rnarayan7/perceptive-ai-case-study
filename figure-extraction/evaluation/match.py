"""Align predictions to gold values.

A seam: the deterministic :class:`ExactKeyMatcher` ships now; an LLM-judge matcher
drops in behind the same interface later for the fuzzy cases where keys do not
line up. The harness depends only on :class:`Matcher`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from evaluation.types import KeyedTruth, Prediction


@dataclass
class MatchResult:
    """Every gold value paired with its prediction (``None`` if the extractor
    missed it), plus any predictions that matched no gold value."""

    pairs: List[Tuple[Optional[Prediction], KeyedTruth]] = field(default_factory=list)
    unmatched_predictions: List[Prediction] = field(default_factory=list)

    @property
    def missed(self) -> int:
        return sum(1 for pred, _ in self.pairs if pred is None)


class Matcher(abc.ABC):
    """Aligns a figure's predictions with its gold values."""

    @abc.abstractmethod
    def match(self, predictions: List[Prediction], truths: List[KeyedTruth]) -> MatchResult:
        raise NotImplementedError


class ExactKeyMatcher(Matcher):
    """Align on the canonical ``quantity_key``. The default, deterministic path."""

    def match(self, predictions: List[Prediction], truths: List[KeyedTruth]) -> MatchResult:
        by_key: Dict[str, Prediction] = {}
        for pred in predictions:
            by_key.setdefault(pred.quantity_key, pred)  # first read of a key wins
        result = MatchResult()
        consumed = set()
        for truth in truths:
            pred = by_key.get(truth.quantity_key)
            if pred is not None:
                consumed.add(truth.quantity_key)
            result.pairs.append((pred, truth))
        result.unmatched_predictions = [
            p for p in predictions if p.quantity_key not in consumed
        ]
        return result


class LLMJudgeMatcher(Matcher):
    """Fuzzy alignment via an LLM judge, for values whose keys do not line up.

    Exact-key matching runs first (free and certain). Only the leftovers, gold
    values the extractor did not key-match and predictions that matched no gold,
    are handed to the judge, which returns which prediction (if any) refers to the
    same quantity as each unmatched truth. The judge only *aligns* labels; the
    same deterministic scorers grade the numbers, so the model never scores.

    The judge is any :class:`~evaluation.llm.VisionClient`, injected for testing.
    For live use pass ``AnthropicClient(model=JUDGE_MODEL)`` (the middle tier, a
    text-alignment task). Without a client it degrades to exact-key matching.
    """

    def __init__(self, client=None) -> None:
        self._client = client

    def match(self, predictions: List[Prediction], truths: List[KeyedTruth]) -> MatchResult:
        base = ExactKeyMatcher().match(predictions, truths)
        unmatched_truths = [t for pred, t in base.pairs if pred is None]
        leftover_preds = list(base.unmatched_predictions)
        if not unmatched_truths or not leftover_preds or self._client is None:
            return base

        alignment = self._ask_judge(leftover_preds, unmatched_truths)
        pred_by_key = {p.quantity_key: p for p in leftover_preds}
        used: set = set()
        new_pairs: List[Tuple[Optional[Prediction], KeyedTruth]] = []
        for pred, truth in base.pairs:
            if pred is None and truth.quantity_key in alignment:
                candidate = pred_by_key.get(alignment[truth.quantity_key])
                if candidate is not None:
                    pred = candidate
                    used.add(candidate.quantity_key)
            new_pairs.append((pred, truth))
        return MatchResult(
            pairs=new_pairs,
            unmatched_predictions=[p for p in leftover_preds if p.quantity_key not in used],
        )

    def _ask_judge(self, preds: List[Prediction], truths: List[KeyedTruth]) -> Dict[str, str]:
        """Return {truth.quantity_key: prediction.quantity_key} for aligned pairs."""
        import json as _json

        from evaluation.llm import extract_json

        truth_lines = [f'  {t.quantity_key}: "{t.value_raw[:80]}"' for t in truths]
        pred_lines = [f'  {p.quantity_key}: "{p.value_raw[:80]}"' for p in preds]
        prompt = (
            "Align each GOLD quantity to the PREDICTION that refers to the same "
            "measured quantity, if any. Match on meaning, not wording. Return ONLY "
            'a JSON object mapping gold key to prediction key, e.g. {"gold_key": '
            '"pred_key"}. Omit a gold key with no match.\n\n'
            "GOLD:\n" + "\n".join(truth_lines) + "\n\nPREDICTIONS:\n" + "\n".join(pred_lines)
        )
        try:
            reply = self._client.complete(prompt, max_tokens=512)
            data = extract_json(reply)
        except Exception:  # noqa: BLE001 - a judge failure falls back to exact-only
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
