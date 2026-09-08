"""Validating the faithfulness judge itself (meta-eval).

Before trusting the judge's scores on real memos, we check the judge:
- ``run_probes``: known-answer probes (hand-labeled claim+evidence). If the judge cannot
  score these correctly, its numbers are not trustworthy.
- ``known_bad_baseline``: grade a memo's real claims, then grade a corrupted version
  (each claim reassigned another claim's evidence). Faithfulness must collapse; if a
  broken memo scores as well as a real one, the eval measures nothing.
- ``sample_calibration`` / ``score_calibration``: sample real claims for a human to label,
  then measure judge-vs-human agreement (the true anchor).

All judging reuses the same :class:`FaithfulnessEvaluator` path being validated.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from memo.analysis.base import AnalysisResult, Claim, Evidence
from memo.eval.faithfulness import FaithfulnessEvaluator

#: A verdict counts as "faithful" (coarse binary) if the claim is at least partly supported.
FAITHFUL = {"supported", "partial"}
VERDICTS = {"supported", "partial", "unsupported", "contradicted"}


@dataclass
class Probe:
    id: str
    claim: str
    evidence: List[str]
    expected: str


def load_probes(path: str) -> List[Probe]:
    payload = json.loads(Path(path).read_text())
    return [Probe(c["id"], c["claim"], list(c["evidence"]), c["expected"])
            for c in payload["cases"]]


def _verdict(judge, claim_text: str, quotes: List[str]) -> str:
    """Judge one claim against evidence quotes, via the real faithfulness path."""
    evidence = [Evidence(doc_id="probe", source="probe", doc_type="probe", url="", quote=q)
                for q in quotes]
    claim = Claim(statement=claim_text, confidence=1.0, rationale="", evidence=evidence)
    report = FaithfulnessEvaluator(AnalysisResult("PROBE", "probe", "", [claim]), judge).run()
    return report.case_results[0].detail.get("verdict", "unsupported")


def run_probes(judge, probes: List[Probe]) -> Dict:
    """Score the judge on known-answer probes. Reports exact- and binary-verdict accuracy."""
    rows = []
    exact = binary = 0
    confusion: Dict[str, int] = {}
    for p in probes:
        got = _verdict(judge, p.claim, p.evidence)
        ex_ok = got == p.expected
        bin_ok = (got in FAITHFUL) == (p.expected in FAITHFUL)
        exact += int(ex_ok)
        binary += int(bin_ok)
        rows.append({"id": p.id, "expected": p.expected, "got": got,
                     "exact": ex_ok, "binary": bin_ok})
        key = f"{p.expected}->{got}"
        confusion[key] = confusion.get(key, 0) + 1
    n = len(probes) or 1
    return {"n": len(probes), "exact_accuracy": exact / n, "binary_accuracy": binary / n,
            "confusion": confusion, "rows": rows}


def corrupt_analysis(analysis: AnalysisResult) -> AnalysisResult:
    """Rotate each grounded claim's evidence onto a different claim, breaking citations."""
    grounded = [c for c in analysis.claims if c.evidence]
    if len(grounded) < 2:
        return AnalysisResult(analysis.company, analysis.module, "", [])
    evsets = [c.evidence for c in grounded]
    rotated = evsets[1:] + evsets[:1]
    claims = [Claim(statement=c.statement, confidence=c.confidence, rationale=c.rationale,
                    evidence=e) for c, e in zip(grounded, rotated)]
    return AnalysisResult(analysis.company, analysis.module, "", claims)


def known_bad_baseline(analysis: AnalysisResult, judge) -> Dict:
    """Real faithfulness vs corrupted faithfulness. The gap is the eval's discrimination."""
    grounded = AnalysisResult(analysis.company, analysis.module, "",
                              [c for c in analysis.claims if c.evidence])
    corrupt = corrupt_analysis(analysis)
    real = (FaithfulnessEvaluator(grounded, judge).run().aggregate.get("faithfulness")
            if grounded.claims else None)
    bad = (FaithfulnessEvaluator(corrupt, judge).run().aggregate.get("faithfulness")
           if corrupt.claims else None)
    return {"n": len(grounded.claims), "real": real, "corrupted": bad,
            "gap": (real - bad) if (real is not None and bad is not None) else None,
            "discriminates": bool(real is not None and bad is not None and real - bad > 0.2)}


def sample_calibration(claims_with_meta: List[Dict], n: int, seed: int = 0) -> List[Dict]:
    """Pick n grounded claims for a human to label (blank ``human_verdict`` to fill)."""
    pool = [c for c in claims_with_meta if c.get("evidence")]
    random.Random(seed).shuffle(pool)
    return [{"claim_id": c["claim_id"], "company": c["company"], "module": c["module"],
             "statement": c["statement"], "evidence": c["evidence"],
             "human_verdict": ""} for c in pool[:n]]


def _cohen_kappa(pairs: List) -> Optional[float]:
    """Cohen's kappa on binary (faithful/unfaithful) human-vs-judge agreement."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa1 = sum(1 for a, _ in pairs if a) / n
    pb1 = sum(1 for _, b in pairs if b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    return None if pe == 1.0 else (po - pe) / (1 - pe)


def score_calibration(labeled: List[Dict], judge) -> Dict:
    """Run the judge on human-labeled claims; report agreement and Cohen's kappa (binary)."""
    rows = []
    pairs = []
    for item in labeled:
        hv = (item.get("human_verdict") or "").strip().lower()
        if hv not in VERDICTS:
            continue  # unlabeled row, skip
        got = _verdict(judge, item["statement"], item["evidence"])
        h_bin, g_bin = hv in FAITHFUL, got in FAITHFUL
        pairs.append((h_bin, g_bin))
        rows.append({"human": hv, "judge": got, "agree": h_bin == g_bin})
    n = len(rows) or 1
    return {"n": len(rows), "agreement": sum(r["agree"] for r in rows) / n,
            "kappa": _cohen_kappa(pairs), "rows": rows}
