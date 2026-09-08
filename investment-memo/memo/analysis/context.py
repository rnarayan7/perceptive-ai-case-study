"""The shared services an analysis module operates over.

An :class:`AnalysisContext` bundles everything a module needs for one company: the
retriever (prose evidence), the structured store (typed fields), and the model client
(reasoning). Modules take a context rather than reaching for globals, so the retriever
or model can be swapped in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from memo.analysis.model import ModelClient
from memo.ingestion.base import Storage
from memo.rag import Retriever, StructuredStore, build_retriever


@dataclass
class AnalysisContext:
    company: str
    retriever: Retriever
    structured: StructuredStore
    model: ModelClient

    @classmethod
    def for_company(
        cls,
        company: str,
        model: ModelClient,
        storage: Optional[Storage] = None,
    ) -> "AnalysisContext":
        """Build a context for a company from ingested data on disk plus a model client."""
        storage = storage or Storage()
        return cls(
            company=company,
            retriever=build_retriever(company, storage=storage),
            structured=StructuredStore(company, storage=storage),
            model=model,
        )
