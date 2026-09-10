"""Read-side summary of what has been ingested, per source.

Powers the Activity page's "Ingested datasets" section: one row per dataset that is
actually present in the corpus, with when it was last pulled and how fresh the
underlying data is. The per-source/company ingestion manifests
(``data/<company>/<source>/_manifest.json``) are the source of truth for the run
timestamp and the document set; each manifest record also carries the document's
``published`` and ``retrieved_at`` dates, so no full-text load is needed here.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from memo.ingestion import REGISTRY

from app.deps import DATA_ROOT
from app.registry import ORDER

# Human-friendly names for each source key. Kept accurate to what each ingester pulls.
SOURCE_LABELS: Dict[str, str] = {
    "edgar": "SEC EDGAR filings",
    "clinicaltrials": "ClinicalTrials.gov",
    "pubmed": "PubMed",
    "openfda": "openFDA",
    "cms": "CMS Part D spending",
    "nadac": "NADAC drug pricing",
    "preprints": "Preprints (bioRxiv/medRxiv)",
    "cdc": "CDC epidemiology",
    "pubchem": "PubChem",
    "xbrl": "XBRL financial facts",
    "asp": "Medicare ASP pricing",
    "orphanet": "Orphanet rare-disease registry",
}

# Static on disk for v1 (ingestion runs offline); cache the summary once per process.
_CACHE: Optional[List[Dict[str, Any]]] = None
_LOCK = threading.Lock()


def _read_manifest(company: str, source: str) -> Optional[Dict[str, Any]]:
    path = DATA_ROOT / company / source / "_manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _build() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for source in REGISTRY:
        doc_count = 0
        company_count = 0
        last_ingested: Optional[str] = None
        latest_item: Optional[str] = None

        for company in ORDER:
            manifest = _read_manifest(company, source)
            if not manifest:
                continue
            docs = manifest.get("documents", [])
            if not docs:
                continue
            company_count += 1
            doc_count += len(docs)

            # "When ingested": prefer the run's finish time, fall back to start.
            ingested = manifest.get("finished_at") or manifest.get("started_at")
            if ingested and (last_ingested is None or ingested > last_ingested):
                last_ingested = ingested

            for doc in docs:
                published = doc.get("published")
                if published and (latest_item is None or published > latest_item):
                    latest_item = published

        # Only surface sources actually present in the corpus.
        if doc_count == 0:
            continue

        rows.append({
            "source": source,
            "label": SOURCE_LABELS.get(source, source),
            "doc_count": doc_count,
            "company_count": company_count,
            "last_ingested": last_ingested,
            "latest_item": latest_item,
        })

    # Freshest pull first; rows without a timestamp sort last.
    rows.sort(key=lambda r: r["last_ingested"] or "", reverse=True)
    return rows


def ingestion_summary() -> List[Dict[str, Any]]:
    """One row per dataset present in the corpus, newest pull first."""
    global _CACHE
    if _CACHE is None:
        with _LOCK:
            if _CACHE is None:
                _CACHE = _build()
    return _CACHE
