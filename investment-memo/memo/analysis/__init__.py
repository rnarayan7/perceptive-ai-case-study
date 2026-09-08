"""Analysis modules: turn retrieved evidence into typed, cited claims.

Each analytical question is an :class:`AnalysisModule` discoverable through
:data:`REGISTRY`. MoA is built; PoS, regulatory, peak sales, and price follow the same
interface. The first real model calls in the system live here.
"""

from typing import Dict, Type

from memo.analysis.base import AnalysisModule, AnalysisResult, Claim, Evidence
from memo.analysis.context import AnalysisContext
from memo.analysis.model import (
    AnthropicModelClient,
    ModelClient,
    ModelRefusal,
    ModelResponse,
    default_model_client,
)
from memo.analysis.moa import MechanismModule
from memo.analysis.peak_sales import PeakSalesModule
from memo.analysis.pos import ProbabilityOfSuccessModule
from memo.analysis.price import PriceVsThesisModule
from memo.analysis.regulatory import RegulatoryModule

REGISTRY: Dict[str, Type[AnalysisModule]] = {
    MechanismModule.name: MechanismModule,
    ProbabilityOfSuccessModule.name: ProbabilityOfSuccessModule,
    RegulatoryModule.name: RegulatoryModule,
    PeakSalesModule.name: PeakSalesModule,
    PriceVsThesisModule.name: PriceVsThesisModule,
}

__all__ = [
    "AnalysisModule",
    "AnalysisResult",
    "Claim",
    "Evidence",
    "AnalysisContext",
    "ModelClient",
    "AnthropicModelClient",
    "ModelResponse",
    "ModelRefusal",
    "default_model_client",
    "MechanismModule",
    "ProbabilityOfSuccessModule",
    "RegulatoryModule",
    "PeakSalesModule",
    "PriceVsThesisModule",
    "REGISTRY",
]
