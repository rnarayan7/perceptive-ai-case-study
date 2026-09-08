"""Source ingestion.

Each source is an :class:`~memo.ingestion.base.BaseIngester` subclass that fetches
public data for a company and writes normalized documents to disk.
"""

from typing import Dict, Type

from memo.ingestion.base import (
    BaseIngester,
    Document,
    HttpClient,
    IngestManifest,
    Storage,
)
from memo.ingestion.cdc import CdcIngester
from memo.ingestion.clinicaltrials import ClinicalTrialsIngester
from memo.ingestion.cms import CmsSpendingIngester
from memo.ingestion.edgar import EdgarIngester
from memo.ingestion.nadac import NadacIngester
from memo.ingestion.openfda import OpenFdaIngester
from memo.ingestion.preprints import PreprintsIngester
from memo.ingestion.pubchem import PubChemIngester
from memo.ingestion.pubmed import PubMedIngester

#: Maps a source key to its ingester class. The CLI and any orchestrator should
#: discover sources through this registry rather than importing classes directly,
#: so adding a source is a one-line change here.
REGISTRY: Dict[str, Type[BaseIngester]] = {
    EdgarIngester.source: EdgarIngester,
    ClinicalTrialsIngester.source: ClinicalTrialsIngester,
    PubMedIngester.source: PubMedIngester,
    OpenFdaIngester.source: OpenFdaIngester,
    CmsSpendingIngester.source: CmsSpendingIngester,
    NadacIngester.source: NadacIngester,
    PreprintsIngester.source: PreprintsIngester,
    CdcIngester.source: CdcIngester,
    PubChemIngester.source: PubChemIngester,
}

__all__ = [
    "BaseIngester",
    "Document",
    "HttpClient",
    "IngestManifest",
    "Storage",
    "EdgarIngester",
    "ClinicalTrialsIngester",
    "PubMedIngester",
    "OpenFdaIngester",
    "CmsSpendingIngester",
    "NadacIngester",
    "PreprintsIngester",
    "CdcIngester",
    "PubChemIngester",
    "REGISTRY",
]
