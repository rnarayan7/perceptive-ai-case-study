"""Run the evaluation loop and write a report.

    gold figure -> extractor -> matcher -> parse both -> score -> aggregate

Runs today against the stub extractor, so the scoring is validated before a real
extractor exists. A real extractor implements the same :class:`Extractor`
interface and is swapped in here.

Usage::

    python -m evaluation.run                       # stub over all figures
    python -m evaluation.run --figure fig04_forest # one figure
    python -m evaluation.run --verified-only       # score only verified gold
    python -m evaluation.run --noise 1.5 --seed 3  # tune the stub
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

from evaluation import metrics
from evaluation.extractors import Extractor, StubExtractor
from evaluation.keys import spec_for
from evaluation.match import ExactKeyMatcher, Matcher
from evaluation.parse import parse_value
from evaluation.scorers import score
from evaluation.types import GoldFigure, KeyedTruth, ScoreResult

DEFAULT_GOLD = Path(__file__).parent / "gold" / "reference_figures.json"
DEFAULT_CV_CONFIG = Path(__file__).parent / "cv" / "cv_config.json"


def load_gold(path: Path, verified_only: bool = False) -> List[GoldFigure]:
    """Load the gold JSON into :class:`GoldFigure` objects."""
    data = json.loads(Path(path).read_text())
    figures: List[GoldFigure] = []
    for figure_id, block in data.items():
        if figure_id.startswith("_"):
            continue
        truths: List[KeyedTruth] = []
        for key, spec in (block.get("truths") or {}).items():
            verified = bool(spec.get("verified", False))
            if verified_only and not verified:
                continue
            qspec = spec_for(figure_id, key)
            family = qspec.family if qspec else "continuous"
            truths.append(KeyedTruth(
                figure_id=figure_id,
                figure_type=block["figure_type"],
                quantity_key=key,
                family=family,
                value_raw=str(spec.get("value", "")),
                unit=spec.get("unit"),
                verified=verified,
            ))
        figures.append(GoldFigure(
            figure_id=figure_id,
            figure_type=block["figure_type"],
            image_path=block.get("image"),
            truths=truths,
        ))
    return figures


def evaluate(
    figures: List[GoldFigure],
    extractor: Extractor,
    matcher: Optional[Matcher] = None,
) -> "tuple[List[ScoreResult], int]":
    """Run extract -> match -> score over every figure; return results and misses."""
    matcher = matcher or ExactKeyMatcher()
    results: List[ScoreResult] = []
    missed = 0
    for figure in figures:
        try:
            predictions = extractor.extract(figure)
        except Exception as exc:  # noqa: BLE001 - a per-figure failure is a miss, not a crash
            print(f"  ! extract failed for {figure.figure_id}: {exc}")
            predictions = []
        match_result = matcher.match(predictions, figure.truths)
        for pred, truth in match_result.pairs:
            qspec = spec_for(truth.figure_id, truth.quantity_key)
            if qspec is None:
                continue
            if pred is None:
                missed += 1
                results.append(ScoreResult(
                    figure_id=truth.figure_id, figure_type=truth.figure_type,
                    quantity_key=truth.quantity_key, family=truth.family,
                    predicted_raw="", truth_raw=truth.value_raw,
                    within_tolerance=False, error_category="missed",
                ))
                continue
            pred_val = parse_value(pred.value_raw, pred.unit, qspec.family)
            truth_val = parse_value(truth.value_raw, truth.unit, qspec.family)
            result = score(pred, pred_val, truth_val, qspec)
            result.details["verified_gold"] = truth.verified
            results.append(result)
    return results, missed


def _load_cv_configs(path: Path) -> Dict[str, dict]:
    """Load the per-figure CV config, or an empty map when it is absent."""
    if not path.exists():
        return {}
    return json.loads(Path(path).read_text())


def _build_extractor(args) -> Extractor:
    if args.extractor == "vlm":
        from evaluation.extractor_vlm import VlmExtractor
        return VlmExtractor(samples=args.samples)
    if args.extractor == "combined":
        # Two independent reads per geometric quantity: the VLM read and a pixel
        # measurement (CV). Their agreement is the keystone triage signal, and the
        # CV read rescues figures the VLM alone misreads (e.g. the waterfall).
        from evaluation.cv.extractor import CombinedExtractor, CvExtractor
        from evaluation.extractor_vlm import VlmExtractor
        vlm = VlmExtractor(samples=args.samples, base_dir=args.base_dir)
        cv = CvExtractor(_load_cv_configs(args.cv_config), base_dir=args.base_dir)
        return CombinedExtractor(vlm, cv, prefer_cv=not args.prefer_vlm)
    return StubExtractor(noise=args.noise, seed=args.seed)


def _router_accuracy(figures: List[GoldFigure], routed: Dict[str, str]) -> Dict[str, object]:
    correct = sum(1 for f in figures if routed.get(f.figure_id) == f.figure_type)
    total = len([f for f in figures if f.figure_id in routed])
    return {"correct": correct, "total": total,
            "accuracy": round(correct / total, 3) if total else None,
            "routed": routed}


def _run_cost(extractor) -> Optional[dict]:
    """Token cost of the run, summed over the extractor's model clients.

    Reaches the VLM client(s) whether the extractor is a VlmExtractor or a
    CombinedExtractor (whose .vlm holds them). None for the stub (no model).
    """
    from evaluation.llm import total_cost
    vlm = getattr(extractor, "vlm", extractor)
    clients = [c for c in (getattr(vlm, "client", None), getattr(vlm, "router_client", None))
               if c is not None and hasattr(c, "cost")]
    return total_cost(*clients) if clients else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="evaluation.run", description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--figure", default=None, help="score one figure id only")
    parser.add_argument("--verified-only", action="store_true",
                        help="score only verified gold values")
    parser.add_argument("--extractor", choices=["stub", "vlm", "combined"], default="stub",
                        help="stub (default, no model), vlm (needs ANTHROPIC_API_KEY), "
                             "or combined (vlm + pixel-measurement CV cross-check)")
    parser.add_argument("--samples", type=int, default=2,
                        help="vlm: independent read samples per value (1 is faster)")
    parser.add_argument("--cv-config", type=Path, default=DEFAULT_CV_CONFIG,
                        help="combined: per-figure CV config (colors, axes, baseline)")
    parser.add_argument("--base-dir", type=Path, default=Path("."),
                        help="root the gold image paths resolve against")
    parser.add_argument("--prefer-vlm", action="store_true",
                        help="combined: make the VLM read primary for geometric keys "
                             "(default: the CV measurement is primary)")
    parser.add_argument("--noise", type=float, default=1.0, help="stub noise scale")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("data/eval_report.json"))
    parser.add_argument("--json", action="store_true", help="print the raw report JSON")
    args = parser.parse_args(argv)

    figures = load_gold(args.gold, verified_only=args.verified_only)
    if args.figure:
        figures = [f for f in figures if f.figure_id == args.figure]
        if not figures:
            parser.error(f"unknown figure id {args.figure!r}")

    extractor = _build_extractor(args)
    results, missed = evaluate(figures, extractor)
    report = metrics.aggregate(results, missed=missed)
    if getattr(extractor, "routed", None):
        report["router"] = _router_accuracy(figures, extractor.routed)
    cost = _run_cost(extractor)
    if cost and cost["input_tokens"]:
        report["cost"] = cost

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(metrics.format_report(report))
        if cost and cost["input_tokens"]:
            per_fig = cost["total_usd"] / max(len(figures), 1)
            print(f"\nCost of this run: ${cost['total_usd']:.4f} "
                  f"({cost['input_tokens']} in / {cost['output_tokens']} out tokens), "
                  f"${per_fig:.4f}/figure")
        print(f"\nFull report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
