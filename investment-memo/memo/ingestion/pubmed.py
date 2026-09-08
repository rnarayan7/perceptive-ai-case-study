"""PubMed ingestion via NCBI E-utilities.

Scientific abstracts are the mechanism-of-action evidence that filings and trial
registries lack: they describe the target, the degradation/inhibition mechanism, and
the pharmacodynamic readouts. Two requests per run (esearch for PMIDs, one efetch for
their abstracts), so rate limiting is a non-issue. Free and keyless.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, List, Optional
from urllib.parse import urlencode

from memo.ingestion.base import BaseIngester, Document

# Asset codes like "KT-474" or "SAR444656" - the precise terms that surface a program's
# mechanism papers, far better than a company name (which matches co-authored trials).
_DRUG_CODE = re.compile(r"\b[A-Z]{2,6}-?\d{3,}[A-Z0-9]*\b")

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_TOOL = "perceptive-memo-research"

# Same sponsor mapping used elsewhere; PubMed matches the name across all fields
# (author affiliations, text), which surfaces the company's own publications.
_SPONSORS = {
    "ABVX": "Abivax",
    "KYMR": "Kymera Therapeutics",
    "PRAX": "Praxis Precision Medicines",
    "IMVT": "Immunovant",
    "COGT": "Cogent Biosciences",
}


class PubMedIngester(BaseIngester):
    source = "pubmed"

    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Fetch abstracts for ``company``. Options: ``term`` (override query), ``limit``."""
        term = options.get("term") or self._default_term(company)
        limit = int(options.get("limit", 25))

        pmids = self._search(term, limit)
        if not pmids:
            return []
        return self._fetch_abstracts(company, pmids)

    # ---------------------------------------------------------------- internals

    def _default_term(self, company: str) -> str:
        """Prefer a query built from the company's asset codes; fall back to the name.

        Asset codes come from already-ingested clinical trials (their intervention
        names), which makes this company-agnostic and precise. Read directly through
        Storage to avoid a dependency on the RAG layer.
        """
        codes = self._drug_codes(company)
        if codes:
            return " OR ".join(sorted(codes))

        ticker = company.strip().upper()
        sponsor = _SPONSORS.get(ticker)
        if not sponsor:
            raise ValueError(f"no asset codes or sponsor mapping for {ticker}; pass term=")
        return sponsor

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

    def _search(self, term: str, limit: int) -> List[str]:
        params = urlencode({
            "db": "pubmed", "term": term, "retmax": limit,
            "retmode": "json", "sort": "relevance", "tool": _TOOL,
        })
        payload = self.http.get_json(f"{_ESEARCH}?{params}")
        return payload.get("esearchresult", {}).get("idlist", [])

    def _fetch_abstracts(self, company: str, pmids: List[str]) -> List[Document]:
        params = urlencode({
            "db": "pubmed", "id": ",".join(pmids),
            "retmode": "xml", "rettype": "abstract", "tool": _TOOL,
        })
        xml_text = self.http.get_text(f"{_EFETCH}?{params}")
        root = ET.fromstring(xml_text)

        documents: List[Document] = []
        for article in root.findall(".//PubmedArticle"):
            document = self._to_document(company, article)
            if document is not None:
                documents.append(document)
        return documents

    def _to_document(self, company: str, article: ET.Element) -> Optional[Document]:
        pmid = _text(article, ".//MedlineCitation/PMID")
        if not pmid:
            return None
        title = _text(article, ".//Article/ArticleTitle") or f"PMID {pmid}"
        abstract = self._abstract_text(article)
        journal = _text(article, ".//Article/Journal/Title")
        year = _text(article, ".//Article/Journal/JournalIssue/PubDate/Year")

        text_parts = [f"Title: {title}"]
        if journal:
            text_parts.append(f"Journal: {journal}")
        if abstract:
            text_parts.append(f"Abstract: {abstract}")
        text = "\n".join(text_parts)

        return Document(
            company=company,
            source=self.source,
            doc_type="article",
            doc_id=pmid,
            title=title,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            published=year,
            metadata={"journal": journal, "year": year, "pmid": pmid},
            text=text,
            raw=ET.tostring(article, encoding="unicode"),
        )

    @staticmethod
    def _abstract_text(article: ET.Element) -> str:
        """Join all AbstractText sections, prefixing labeled ones (e.g. 'METHODS:')."""
        parts = []
        for node in article.findall(".//Article/Abstract/AbstractText"):
            body = "".join(node.itertext()).strip()
            if not body:
                continue
            label = node.get("Label")
            parts.append(f"{label}: {body}" if label else body)
        return " ".join(parts)


def _text(element: ET.Element, path: str) -> str:
    node = element.find(path)
    if node is None:
        return ""
    return "".join(node.itertext()).strip()
