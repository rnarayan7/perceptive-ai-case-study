"""Preprint ingestion via Europe PMC (bioRxiv/medRxiv).

Preprints carry the earliest public mechanism-of-action and translational evidence
for a program - target engagement, degradation/inhibition data, and pharmacodynamic
readouts that appear months ahead of peer review or a filing. Europe PMC is the entry
point: one keyless JSON search that indexes both bioRxiv and medRxiv, filtered to
preprint sources so nothing peer-reviewed leaks in. Single request per run.

Queries are built from the company's own asset codes (the intervention names of
already-ingested clinical trials), which is far more precise than a company name.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from memo.ingestion.base import BaseIngester, Document

# Asset codes like "KT-474" or "SAR444656" - the precise terms that surface a
# program's mechanism papers. Same shape the PubMed ingester uses.
_DRUG_CODE = re.compile(r"\b[A-Z]{2,6}-?\d{3,}[A-Z0-9]*\b")

# VERIFIED keyless JSON REST endpoint (feasibility doc, checked 2026-09-07).
_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Europe PMC source filter: SRC:PPR restricts results to preprint servers
# (bioRxiv, medRxiv, Research Square, etc.), which is exactly the preprint corpus.
_PREPRINT_FILTER = "SRC:PPR"

# Fallback when a company has no ingested trials to derive codes from. Europe PMC
# matches the name across author affiliations and text.
_SPONSORS = {
    "ABVX": "Abivax",
    "KYMR": "Kymera Therapeutics",
    "PRAX": "Praxis Precision Medicines",
    "IMVT": "Immunovant",
    "COGT": "Cogent Biosciences",
}


class _AbstractTextExtractor(HTMLParser):
    """Reduce an HTML abstract fragment to readable plain text.

    Europe PMC ``abstractText`` often arrives with inline markup, e.g.
    ``<h4>ABSTRACT</h4> Acute Myeloid Leukemia ... <i>degraders</i>``. Left in
    place, tags become junk BM25 tokens (``h4``, ``i``) that pollute retrieval
    and citations. This is deliberately stdlib-only, matching edgar.py's
    ``_HTMLTextExtractor`` pattern and base.py's no-extra-packages ethos.

    A space is emitted for every tag so words on either side never fuse, and
    ``convert_charrefs=True`` unescapes entities (``&amp;`` -> ``&``) inside
    text automatically. Whitespace is collapsed by the caller.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


# Runs of whitespace (including the spaces inserted for tags) collapse to one.
_WS = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """Strip HTML tags and unescape entities, preserving readable words.

    A no-op-equivalent for already-plain text (no tags, no entities). Tags are
    replaced with whitespace so adjacent words stay separated, then whitespace
    is collapsed to single spaces and trimmed.
    """
    if not text:
        return ""
    parser = _AbstractTextExtractor()
    parser.feed(text)
    parser.close()
    return _WS.sub(" ", parser.get_text()).strip()


class PreprintsIngester(BaseIngester):
    source = "preprints"

    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Fetch preprints for ``company``.

        Options: ``term`` (override the derived query), ``limit`` (default 25).
        An empty or unresolvable query returns ``[]`` rather than raising.
        """
        limit = int(options.get("limit", 25))
        term = options.get("term") or self._default_term(company)
        if not term:
            return []

        results = self._search(term, limit)
        documents: List[Document] = []
        for result in results:
            document = self._to_document(company, result)
            if document is not None:
                documents.append(document)
        return documents

    # ---------------------------------------------------------------- internals

    def _default_term(self, company: str) -> str:
        """Query built from asset codes, falling back to the sponsor name.

        Asset codes come from already-ingested clinical trials (their intervention
        names), which keeps this company-agnostic and precise. Read directly through
        Storage to avoid a dependency on the RAG layer. Returns "" when neither
        codes nor a sponsor mapping resolve, so the caller returns an empty result.
        """
        codes = self._drug_codes(company)
        if codes:
            return " OR ".join(sorted(codes))

        return _SPONSORS.get(company.strip().upper(), "")

    def _drug_codes(self, company: str, cap: int = 12) -> List[str]:
        codes: List[str] = []
        seen = set()
        for doc in self.storage.load_documents(company, source="clinicaltrials"):
            for item in (doc.metadata or {}).get("interventions", []):
                name = item.get("name", "") if isinstance(item, dict) else str(item)
                for match in _DRUG_CODE.findall(name):
                    if match not in seen:
                        seen.add(match)
                        codes.append(match)
        return codes[:cap]

    def _search(self, term: str, limit: int) -> List[Dict[str, Any]]:
        """One Europe PMC search, restricted to preprint sources."""
        query = f"({term}) AND {_PREPRINT_FILTER}"
        params = urlencode({
            "query": query,
            "format": "json",
            "resultType": "core",  # includes abstractText
            "pageSize": max(1, min(limit, 100)),
            "sort": "P_PDATE_D desc",  # most recent first
        })
        payload = self.http.get_json(f"{_SEARCH}?{params}")
        return (payload.get("resultList") or {}).get("result") or []

    def _to_document(self, company: str, result: Dict[str, Any]) -> Optional[Document]:
        epmc_id = str(result.get("id") or "").strip()
        doi = str(result.get("doi") or "").strip()
        doc_id = epmc_id or doi
        if not doc_id:
            return None

        title = str(result.get("title") or "").strip() or doc_id
        abstract = _strip_html(str(result.get("abstractText") or ""))
        published = (
            result.get("firstPublicationDate")
            or result.get("pubYear")
            or None
        )
        source_code = str(result.get("source") or "").strip()

        text_parts = [f"Title: {title}"]
        if abstract:
            text_parts.append(f"Abstract: {abstract}")
        text = "\n".join(text_parts)

        return Document(
            company=company,
            source=self.source,
            doc_type="preprint",
            doc_id=doc_id,
            title=title,
            url=self._url(source_code, epmc_id, doi),
            published=published if published is None else str(published),
            metadata={
                "doi": doi,
                "epmc_id": epmc_id,
                "epmc_source": source_code,
                "authors": result.get("authorString"),
                "cited_by": result.get("citedByCount"),
            },
            text=text,
            raw=json.dumps(result),
        )

    @staticmethod
    def _url(source_code: str, epmc_id: str, doi: str) -> str:
        """Prefer the Europe PMC article page; fall back to the DOI resolver."""
        if source_code and epmc_id:
            return f"https://europepmc.org/article/{source_code}/{epmc_id}"
        if doi:
            return f"https://doi.org/{doi}"
        return "https://europepmc.org/"
