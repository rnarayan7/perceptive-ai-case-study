"""Evaluation harness.

Layered evals: deterministic ground-truth evals first (retrieval), model-backed evals
(faithfulness, analytical quality) next. Model-backed evals always call real models,
never mocks. See MEMORY / the architecture doc for the full planned set.
"""

from memo.eval.base import Evaluator
from memo.eval.extraction import (
    ExtractionEvaluator,
    ExtractionGoldSet,
    derive_gold,
    load_extraction_gold,
)
from memo.eval.epi import (
    EpiGroundingEvaluator,
    EpiReference,
    EpiRetrievalEvaluator,
    load_epi_reference,
)
from memo.eval.faithfulness import FaithfulnessEvaluator, verdict_metrics
from memo.eval.goldset import GoldSet, load_goldset, load_goldset_for
from memo.eval.judge_validation import (
    Probe,
    known_bad_baseline,
    load_probes,
    run_probes,
    sample_calibration,
    score_calibration,
)
from memo.eval.quality import QualityEvaluator, dimension_metrics
from memo.eval.report import format_report, save_report
from memo.eval.retrieval import RetrievalEvaluator
from memo.eval.types import CaseResult, EvalReport

__all__ = [
    "Evaluator",
    "GoldSet",
    "load_goldset",
    "load_goldset_for",
    "RetrievalEvaluator",
    "EpiRetrievalEvaluator",
    "EpiGroundingEvaluator",
    "EpiReference",
    "load_epi_reference",
    "FaithfulnessEvaluator",
    "verdict_metrics",
    "QualityEvaluator",
    "dimension_metrics",
    "Probe",
    "load_probes",
    "run_probes",
    "known_bad_baseline",
    "sample_calibration",
    "score_calibration",
    "ExtractionEvaluator",
    "ExtractionGoldSet",
    "derive_gold",
    "load_extraction_gold",
    "EvalReport",
    "CaseResult",
    "format_report",
    "save_report",
]
