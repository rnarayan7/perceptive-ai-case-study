"""Render and persist eval reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from memo.eval.types import EvalReport

DEFAULT_RESULTS_ROOT = Path("evals") / "results"


def format_report(report: EvalReport) -> str:
    """Human-readable summary: aggregate metrics then per-case detail."""
    lines: List[str] = []
    params = ", ".join(f"{k}={v}" for k, v in report.params.items())
    lines.append(f"{report.name} eval  |  {report.company}  |  {params}")
    lines.append("=" * 64)

    lines.append("Aggregate:")
    for key, value in report.aggregate.items():
        lines.append(f"  {key:<16} {value:.3f}")

    lines.append("")
    lines.append("Per case:")
    for case in report.case_results:
        summary = "  ".join(f"{k}={v:.2f}" for k, v in case.metrics.items())
        lines.append(f"  [{case.case_id}] {summary}")
        lines.append(f"      q: {case.query}")
        if case.detail.get("verdict"):
            lines.append(f"      verdict: {case.detail['verdict']}  ({case.detail.get('rationale','')[:120]})")
        else:
            lines.append(f"      relevant:  {case.relevant}")
            lines.append(f"      retrieved: {case.retrieved[:6]}")
    return "\n".join(lines)


def save_report(report: EvalReport, root: Path = DEFAULT_RESULTS_ROOT) -> Path:
    """Write the report as timestamped JSON for regression tracking over time."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{report.name}_{report.company}_{stamp}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2))
    return path
