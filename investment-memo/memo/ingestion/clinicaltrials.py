"""ClinicalTrials.gov ingestion.

Fetches a sponsor's registered studies from the ClinicalTrials.gov v2 REST API
and normalizes each one into a :class:`~memo.ingestion.base.Document`. The v2
API returns studies as JSON with a nested ``protocolSection`` split into modules
(identification, status, sponsor, conditions, design, arms/interventions,
outcomes, ...); this module distills the fields a biopharma analyst cares about
into ``metadata`` and a readable ``text`` summary, keeping the untouched JSON in
``raw``.

Only the standard library and the shared :class:`HttpClient` are used.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .base import BaseIngester, Document

# API entry point for the v2 study search.
API_BASE = "https://clinicaltrials.gov/api/v2/studies"

# Public study URL, formatted with an NCT id.
STUDY_URL = "https://clinicaltrials.gov/study/{nct}"

# Ticker -> lead-sponsor name for the five case-study companies. Overridable per
# call with the ``sponsor=`` option.
TICKER_TO_SPONSOR: Dict[str, str] = {
    "ABVX": "Abivax",
    "KYMR": "Kymera Therapeutics",
    "PRAX": "Praxis Precision Medicines",
    "IMVT": "Immunovant",
    "COGT": "Cogent Biosciences",
}

# Default cap on studies pulled in one run, to keep runs bounded.
DEFAULT_LIMIT = 100

# Studies per API page (the v2 API allows up to 1000; 100 is a polite default).
DEFAULT_PAGE_SIZE = 100


class ClinicalTrialsIngester(BaseIngester):
    """Ingest a sponsor's studies from the ClinicalTrials.gov v2 API.

    ``fetch`` takes a ticker (e.g. ``"KYMR"``), resolves it to a sponsor company
    name, searches the API by sponsor, paginates through the results, and returns
    one :class:`Document` per study.

    Options accepted by :meth:`fetch` (and therefore :meth:`run`):

    ``sponsor``
        Override the sponsor name instead of relying on the ticker mapping.
    ``limit``
        Maximum number of studies to return (default :data:`DEFAULT_LIMIT`).
    ``page_size``
        Studies requested per API page (default :data:`DEFAULT_PAGE_SIZE`).
    """

    source = "clinicaltrials"

    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Fetch studies for ``company`` (a ticker) and return normalized docs."""
        sponsor = self._resolve_sponsor(company, options.get("sponsor"))
        limit = int(options.get("limit") or DEFAULT_LIMIT)
        page_size = int(options.get("page_size") or DEFAULT_PAGE_SIZE)

        documents: List[Document] = []
        for study in self._iter_studies(sponsor, page_size=page_size, limit=limit):
            document = self._to_document(company, study)
            if document is not None:
                documents.append(document)
        return documents

    # -- sponsor resolution -------------------------------------------------

    def _resolve_sponsor(self, company: str, override: Optional[str]) -> str:
        """Map a ticker to a sponsor name, honoring an explicit override.

        Raises ``ValueError`` when the ticker is unknown and no override is set.
        """
        if override:
            return override
        ticker = (company or "").strip().upper()
        if ticker in TICKER_TO_SPONSOR:
            return TICKER_TO_SPONSOR[ticker]
        raise ValueError(
            f"Unknown ticker {company!r}; pass sponsor='<Company Name>' to override. "
            f"Known tickers: {', '.join(sorted(TICKER_TO_SPONSOR))}."
        )

    # -- fetching / pagination ---------------------------------------------

    def _search_page(
        self,
        sponsor: str,
        page_size: int,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch one page of study results for ``sponsor``."""
        params: Dict[str, str] = {
            "query.spons": sponsor,
            "pageSize": str(page_size),
            "format": "json",
        }
        if page_token:
            params["pageToken"] = page_token
        url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
        response = self.http.get_json(url)
        if not isinstance(response, dict):
            raise RuntimeError(f"Unexpected response for sponsor {sponsor!r}: {type(response)}")
        return response

    def _iter_studies(
        self,
        sponsor: str,
        page_size: int,
        limit: int,
    ) -> Iterator[Dict[str, Any]]:
        """Yield study dicts across pages, stopping at ``limit`` or exhaustion."""
        yielded = 0
        page_token: Optional[str] = None
        while yielded < limit:
            remaining = limit - yielded
            page = self._search_page(sponsor, min(page_size, remaining), page_token)
            studies = page.get("studies") or []
            for study in studies:
                yield study
                yielded += 1
                if yielded >= limit:
                    return
            page_token = page.get("nextPageToken")
            if not page_token or not studies:
                return

    # -- normalization ------------------------------------------------------

    def _to_document(self, company: str, study: Dict[str, Any]) -> Optional[Document]:
        """Convert one raw study into a :class:`Document`, or ``None`` if unusable."""
        protocol = study.get("protocolSection") or {}
        ident = protocol.get("identificationModule") or {}
        nct = ident.get("nctId")
        if not nct:
            return None  # cannot key a document without an NCT id

        status = protocol.get("statusModule") or {}
        title = ident.get("briefTitle") or ident.get("officialTitle") or nct
        published = _get(status, "studyFirstPostDateStruct", "date")

        metadata = self._distill_metadata(protocol)
        text = self._summarize(title, protocol, metadata)

        return Document(
            company=company,
            source=self.source,
            doc_type="study",
            doc_id=nct,
            title=title,
            url=STUDY_URL.format(nct=nct),
            published=published,
            metadata=metadata,
            text=text,
            raw=json.dumps(study, ensure_ascii=False),
        )

    def _distill_metadata(self, protocol: Dict[str, Any]) -> Dict[str, Any]:
        """Pull the analyst-relevant fields out of the nested protocol modules."""
        status = protocol.get("statusModule") or {}
        design = protocol.get("designModule") or {}
        conditions = protocol.get("conditionsModule") or {}
        sponsors = protocol.get("sponsorCollaboratorsModule") or {}
        outcomes = protocol.get("outcomesModule") or {}

        interventions = [
            {"name": iv.get("name"), "type": iv.get("type")}
            for iv in _get_list(protocol, "armsInterventionsModule", "interventions")
            if iv.get("name")
        ]
        primary_outcomes = [
            {"measure": out.get("measure"), "timeFrame": out.get("timeFrame")}
            for out in _get_list(outcomes, "primaryOutcomes")
            if out.get("measure")
        ]

        return {
            "phases": design.get("phases") or [],
            "overall_status": status.get("overallStatus"),
            "study_type": design.get("studyType"),
            "conditions": conditions.get("conditions") or [],
            "interventions": interventions,
            "lead_sponsor": _get(sponsors, "leadSponsor", "name"),
            "enrollment": _get(design, "enrollmentInfo", "count"),
            "start_date": _get(status, "startDateStruct", "date"),
            "primary_completion_date": _get(status, "primaryCompletionDateStruct", "date"),
            "completion_date": _get(status, "completionDateStruct", "date"),
            "first_posted_date": _get(status, "studyFirstPostDateStruct", "date"),
            "primary_outcomes": primary_outcomes,
        }

    def _summarize(
        self,
        title: str,
        protocol: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> str:
        """Assemble a readable plain-text summary for retrieval/indexing."""
        description = protocol.get("descriptionModule") or {}
        lines: List[str] = [f"Title: {title}"]

        if metadata.get("overall_status"):
            lines.append(f"Status: {metadata['overall_status']}")
        if metadata.get("phases"):
            lines.append(f"Phase: {', '.join(metadata['phases'])}")
        if metadata.get("study_type"):
            lines.append(f"Study type: {metadata['study_type']}")
        if metadata.get("lead_sponsor"):
            lines.append(f"Lead sponsor: {metadata['lead_sponsor']}")
        if metadata.get("conditions"):
            lines.append(f"Conditions: {', '.join(metadata['conditions'])}")

        interventions = metadata.get("interventions") or []
        if interventions:
            rendered = ", ".join(
                f"{iv['name']} ({iv['type']})" if iv.get("type") else iv["name"]
                for iv in interventions
            )
            lines.append(f"Interventions: {rendered}")

        if metadata.get("enrollment") is not None:
            lines.append(f"Enrollment: {metadata['enrollment']}")

        brief = description.get("briefSummary")
        if brief:
            lines.append("")
            lines.append(f"Summary: {brief.strip()}")

        primary_outcomes = metadata.get("primary_outcomes") or []
        if primary_outcomes:
            lines.append("")
            lines.append("Primary outcomes:")
            for out in primary_outcomes:
                measure = out.get("measure")
                time_frame = out.get("timeFrame")
                lines.append(f"- {measure}" + (f" (time frame: {time_frame})" if time_frame else ""))

        return "\n".join(lines)


# -- module-level safe-lookup helpers --------------------------------------


def _get(mapping: Any, *keys: str) -> Any:
    """Safely walk nested dicts, returning ``None`` if any step is missing."""
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _get_list(mapping: Any, *keys: str) -> Sequence[Dict[str, Any]]:
    """Like :func:`_get`, but always returns a list of dicts (possibly empty)."""
    value = _get(mapping, *keys)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []
