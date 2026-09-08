"""Figure sources.

Each source is a :class:`~corpus.base.BaseFigureIngester` subclass. The CLI and
any orchestrator discover sources through :data:`REGISTRY`, so adding a source is
a one-line change here.

Depth is deliberately uneven, matching how tractable each source is on public
access alone:

* ``pmc``, ``ctgov``, ``cochraneforest`` are API- or dataset-driven and complete.
* ``fda``, ``edgar_deck`` are seed/PDF-driven and complete for a supplied set of
  documents; the open problem is *discovering* the right documents at scale.
* ``conference`` is a documented scaffold: abstract text is public, but posters
  are gated, so it needs a company-poster-page seed to become useful.
"""

from typing import Dict, Type

from corpus.base import BaseFigureIngester
from corpus.sources.cochraneforest import CochraneForestIngester
from corpus.sources.conference import ConferenceIngester
from corpus.sources.ctgov import ClinicalTrialsTruthIngester
from corpus.sources.edgar_deck import EdgarDeckIngester
from corpus.sources.fda import FdaDocumentIngester
from corpus.sources.pmc import PmcIngester

REGISTRY: Dict[str, Type[BaseFigureIngester]] = {
    PmcIngester.source: PmcIngester,
    ClinicalTrialsTruthIngester.source: ClinicalTrialsTruthIngester,
    CochraneForestIngester.source: CochraneForestIngester,
    FdaDocumentIngester.source: FdaDocumentIngester,
    EdgarDeckIngester.source: EdgarDeckIngester,
    ConferenceIngester.source: ConferenceIngester,
}

__all__ = [
    "REGISTRY",
    "PmcIngester",
    "ClinicalTrialsTruthIngester",
    "CochraneForestIngester",
    "FdaDocumentIngester",
    "EdgarDeckIngester",
    "ConferenceIngester",
]
