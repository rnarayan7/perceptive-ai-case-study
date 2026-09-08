"""Evaluate the extractor against manually-labeled real figures.

This is how the labeling effort becomes real metrics. It loads figures whose
ground truth you verified by hand (via the labeling app), runs a rigorous
free-form extractor on each image, aligns the extractor's predictions to your
labels *by meaning* (the LLM-judge matcher, since real figures have no fixed
quantity schema), and scores deterministically.

Guarding against circular metrics: the extractor here uses a different model and
a more demanding prompt than the labeling suggester, and asks for its own
confidence and interval. Your labels are the arbiter; where you corrected a
suggestion, the metric is genuinely independent. The cleanest signal remains the
figures whose gold came from a recovered public number, which no model produced.

Run (from figure-extraction/, key loaded):
    python3 -m evaluation.corpus_eval --source pmc --type forest
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from corpus.base import FigureStorage, safe_name
from evaluation import metrics
from evaluation.llm import extract_json, media_type_for
from evaluation.match import ExactKeyMatcher, LLMJudgeMatcher, Matcher
from evaluation.parse import parse_value
from evaluation.scorers import score
from evaluation.types import Family, KeyedTruth, Prediction, QuantitySpec, ScoreResult, ValueKind

# Distinct from corpus.label's suggester prompt, on purpose (see module docstring).
_EXTRACT_HINTS = {
    "kaplan_meier": "Report the median for each arm (with units) and the survival probability at each labeled landmark.",
    "forest": "Report each row's hazard/odds/risk ratio with its 95% CI, and the list of rows whose CI crosses 1.",
    "waterfall": "Report the proportion of bars beyond each labeled threshold (state the denominator), the deepest bar, and the leftmost bar.",
    "pk_logscale": "Report the concentration at each labeled timepoint per dose (with units) and any marked threshold.",
    "table": "Report each requested cell value with the denominator it is drawn from.",
    "spider": "Report the implied response/PFS summary the trajectories support (median, landmark).",
}


@dataclass
class LabeledFigure:
    figure_id: str
    figure_type: str
    source: str
    image_path: Path
    truths: List[KeyedTruth] = field(default_factory=list)


# -- family / tolerance inference (no fixed schema for real figures) --------

def infer_family(value_raw: str) -> str:
    # Probe as a proportion: a CI still short-circuits to RATIO, an "n/N" resolves
    # to PROPORTION, everything else (scalars, sentinels) falls to continuous.
    p = parse_value(value_raw or "", family=Family.PROPORTION)
    if p.kind == ValueKind.RATIO:
        return Family.RATIO_CI
    if p.kind == ValueKind.PROPORTION:
        return Family.PROPORTION
    return Family.CONTINUOUS


def infer_tolerance(family: str, truth_raw: str) -> Optional[float]:
    """A defensible pass/fail band when there is no per-quantity spec.

    Proportions are exact; ratios use +-0.1; continuous uses a 10% relative band
    (an absolute band is meaningless across months / % / nM). Reported alongside
    the metrics so the tolerance is explicit.
    """
    if family == Family.PROPORTION:
        return None  # exact numerator+denominator
    if family == Family.RATIO_CI:
        return 0.1
    p = parse_value(truth_raw, None, family)
    if p.scalar:
        return max(abs(p.scalar) * 0.10, 1e-6)
    return None


def _spec_for(truth: KeyedTruth) -> QuantitySpec:
    return QuantitySpec(key=truth.quantity_key, family=truth.family,
                        tolerance=infer_tolerance(truth.family, truth.value_raw))


# -- loading labeled figures ------------------------------------------------

def load_labeled(storage: FigureStorage, source: Optional[str] = None,
                 figure_type: Optional[str] = None) -> List[LabeledFigure]:
    out: List[LabeledFigure] = []
    for fig in storage.load_figures(source=source, figure_type=figure_type):
        verified = [g for g in fig.ground_truth if g.verified]
        if not verified:
            continue
        directory = storage.source_dir(fig.source) / safe_name(fig.figure_id)
        images = list(directory.glob("image.*"))
        if not images:
            continue
        truths = [KeyedTruth(figure_id=fig.figure_id, figure_type=fig.figure_type,
                             quantity_key=g.quantity, family=infer_family(g.value or ""),
                             value_raw=g.value or "", verified=True) for g in verified]
        out.append(LabeledFigure(fig.figure_id, fig.figure_type, fig.source, images[0], truths))
    return out


# -- the extractor under test ----------------------------------------------

class HarvestedExtractor:
    """Free-form extractor for arbitrary real figures: returns Predictions whose
    ``quantity_key`` is the model's own label, aligned to gold by the judge."""

    def __init__(self, client, base_dir: Path = Path(".")) -> None:
        self.client = client
        self.base_dir = Path(base_dir)

    def extract(self, figure: LabeledFigure) -> List[Prediction]:
        hint = _EXTRACT_HINTS.get(figure.figure_type,
                                  "Report every labeled numeric value readable from the figure.")
        prompt = (
            f"You are extracting values from a {figure.figure_type} oncology figure for an "
            f"evaluation. {hint}\n"
            "Read each value carefully off the graphic. Return ONLY a JSON array; each element "
            '{"quantity": "<short label>", "value": "<value with unit>", '
            '"interval_low": <number or null>, "interval_high": <number or null>, '
            '"confidence": <0..1>}. interval bounds express your uncertainty on the value.'
        )
        try:
            reply = self.client.complete(prompt, image=figure.image_path.read_bytes(),
                                         media_type=media_type_for(figure.image_path.suffix),
                                         max_tokens=8000)
        except Exception:  # noqa: BLE001 - a failed figure yields no predictions (all missed)
            return []
        data = extract_json(reply)
        preds: List[Prediction] = []
        if isinstance(data, list):
            for item in data:
                if not (isinstance(item, dict) and item.get("quantity") and item.get("value") is not None):
                    continue
                preds.append(Prediction(
                    figure_id=figure.figure_id, figure_type=figure.figure_type,
                    quantity_key=str(item["quantity"]), value_raw=str(item["value"]),
                    interval_low=_num(item.get("interval_low")),
                    interval_high=_num(item.get("interval_high")),
                    confidence=_num(item.get("confidence")), method="vlm_harvested",
                ))
        return preds


# -- the loop ---------------------------------------------------------------

def evaluate_corpus(figures: List[LabeledFigure], extractor,
                    matcher: Optional[Matcher] = None) -> "tuple[List[ScoreResult], int]":
    matcher = matcher or ExactKeyMatcher()
    results: List[ScoreResult] = []
    missed = 0
    for fig in figures:
        predictions = extractor.extract(fig)
        result = matcher.match(predictions, fig.truths)
        for pred, truth in result.pairs:
            spec = _spec_for(truth)
            if pred is None:
                missed += 1
                results.append(ScoreResult(
                    figure_id=truth.figure_id, figure_type=truth.figure_type,
                    quantity_key=truth.quantity_key, family=truth.family,
                    predicted_raw="", truth_raw=truth.value_raw,
                    within_tolerance=False, error_category="missed"))
                continue
            pv = parse_value(pred.value_raw, None, spec.family)
            tv = parse_value(truth.value_raw, None, spec.family)
            results.append(score(pred, pv, tv, spec))
    return results, missed


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="evaluation.corpus_eval", description=__doc__)
    p.add_argument("--source", help="only this source (pmc, fda, ...)")
    p.add_argument("--type", help="only this figure type")
    p.add_argument("--model", default="claude-opus-5", help="extractor model")
    p.add_argument("--judge-model", default="claude-sonnet-5", help="matcher model")
    p.add_argument("--no-judge", action="store_true", help="exact-key match only (no LLM alignment)")
    p.add_argument("--out", type=Path, default=Path("data/corpus_eval_report.json"))
    args = p.parse_args(argv)

    figures = load_labeled(FigureStorage(), source=args.source, figure_type=args.type)
    if not figures:
        print("No labeled figures found. Label some first with the labeling app.")
        return 0
    n_truths = sum(len(f.truths) for f in figures)
    print(f"Evaluating {len(figures)} labeled figures ({n_truths} verified values)…")

    from evaluation.llm import AnthropicClient
    extractor = HarvestedExtractor(AnthropicClient(model=args.model))
    matcher = ExactKeyMatcher() if args.no_judge else LLMJudgeMatcher(AnthropicClient(model=args.judge_model))

    results, missed = evaluate_corpus(figures, extractor, matcher)
    report = metrics.aggregate(results, missed=missed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(metrics.format_report(report))
    print(f"\nTolerances: proportions exact, hazard ratios +-0.1, continuous 10% relative.")
    print(f"Full report: {args.out}")
    return 0


def _num(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
