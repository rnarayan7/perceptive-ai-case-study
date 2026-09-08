"""NADAC (Medicaid National Average Drug Acquisition Cost) ingestion.

NADAC is the survey-based average invoice price U.S. pharmacies pay to acquire a
drug, published weekly by CMS on data.medicaid.gov. For this memo system it is the
pricing backbone for self-administered / pharmacy-dispensed comparators: Kymera's
assets (KT-474, KT-621, ...) are investigational and have no price of their own, so
NADAC serves the *comparator and generic-floor* side of the peak-sales model.

Access is a keyless JSON GET against the DKAN datastore query API::

    https://data.medicaid.gov/api/1/datastore/query/{dataset_id}/0

The annual NADAC dataset GUID rotates each year, so rather than hardcode a stale id
this module resolves the current dataset at run time from the DKAN search API
(``/api/1/search/?fulltext=NADAC``), picking the dataset whose title carries the
highest year (e.g. "NADAC (National Average Drug Acquisition Cost) 2026"). A known
recent GUID is kept only as a last-resort fallback if resolution fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from memo.ingestion.base import BaseIngester, Document

logger = logging.getLogger("memo.ingestion")

# DKAN endpoints on data.medicaid.gov (keyless JSON).
_SEARCH = "https://data.medicaid.gov/api/1/search"
_DATASTORE_QUERY = "https://data.medicaid.gov/api/1/datastore/query"
_DATASET_PAGE = "https://data.medicaid.gov/dataset"

# Matches the annual NADAC dataset titles, e.g.
# "NADAC (National Average Drug Acquisition Cost) 2026".
_NADAC_YEAR_TITLE = re.compile(
    r"NADAC.*National Average Drug Acquisition Cost.*?(\d{4})", re.IGNORECASE
)

# Last-resort fallback only. Resolution from the search API is preferred and this
# is used solely if that fails; it is the confirmed 2026 annual-file GUID.
_FALLBACK_DATASET_ID = "fbb83258-11c7-47f5-8b18-5f8e79f7e704"

# Column names on the NADAC datastore (verified 2026-09-07).
_COL_DESCRIPTION = "ndc_description"
_COL_PER_UNIT = "nadac_per_unit"
_COL_UNIT = "pricing_unit"
_COL_EFFECTIVE = "effective_date"


class NadacIngester(BaseIngester):
    """Ingest NADAC pharmacy acquisition-cost rows for comparator drugs.

    ``fetch`` options:

    * ``drugs`` (list[str]): NDC descriptions or generic names to match, e.g.
      ``["metformin", "dupilumab"]``. Matching is a case-insensitive substring
      against ``ndc_description``. Defaults to empty (returns ``[]`` with a note).
    * ``limit`` (int): max rows returned per drug term (default 50), newest
      effective dates first.
    * ``dataset_id`` (str): pin a specific dataset GUID, bypassing resolution.
    * ``year`` (int): prefer the annual dataset for this year when resolving.
    """

    source = "nadac"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dataset_cache: Dict[Optional[int], str] = {}

    def fetch(self, company: str, **options: Any) -> List[Document]:
        drugs = _as_str_list(options.get("drugs"))
        if not drugs:
            logger.info(
                "nadac: no 'drugs' option passed for %s; nothing to fetch. "
                "Pass drugs=[...] with generic/NDC descriptions (e.g. ['metformin']).",
                company,
            )
            return []

        limit = int(options.get("limit", 50))
        year = options.get("year")
        year = int(year) if year is not None else None

        dataset_id = options.get("dataset_id") or self._resolve_dataset_id(year)

        documents: List[Document] = []
        seen_ids = set()
        for drug in drugs:
            for row in self._query_rows(dataset_id, drug, limit):
                document = self._to_document(company, dataset_id, drug, row)
                if document is None or document.doc_id in seen_ids:
                    continue
                seen_ids.add(document.doc_id)
                documents.append(document)
        return documents

    # ---------------------------------------------------------------- internals

    def _resolve_dataset_id(self, year: Optional[int] = None) -> str:
        """Resolve the current annual NADAC dataset GUID from the DKAN search API.

        Picks the dataset whose title carries the highest year, or the requested
        ``year`` if present. Falls back to a known recent GUID if the lookup fails.
        """
        if year in self._dataset_cache:
            return self._dataset_cache[year]

        resolved: Optional[str] = None
        try:
            params = urlencode({"fulltext": "NADAC", "page-size": 100})
            payload = self.http.get_json(f"{_SEARCH}/?{params}")
            candidates: List[tuple] = []  # (year, identifier)
            for item in _search_items(payload):
                title = str(item.get("title", ""))
                identifier = item.get("identifier")
                match = _NADAC_YEAR_TITLE.search(title)
                if match and identifier:
                    candidates.append((int(match.group(1)), identifier))
            if candidates:
                if year is not None:
                    for cand_year, identifier in candidates:
                        if cand_year == year:
                            resolved = identifier
                            break
                if resolved is None:
                    resolved = max(candidates, key=lambda c: c[0])[1]
        except Exception as exc:  # noqa: BLE001 - fall back to a known GUID
            logger.warning("nadac: dataset resolution failed (%s); using fallback", exc)

        if resolved is None:
            resolved = _FALLBACK_DATASET_ID
            logger.warning("nadac: no dataset resolved; using fallback %s", resolved)
        else:
            logger.info("nadac: resolved dataset %s", resolved)

        self._dataset_cache[year] = resolved
        return resolved

    def _query_rows(self, dataset_id: str, drug: str, limit: int) -> List[Dict[str, Any]]:
        """Query the datastore for rows whose description contains ``drug``.

        Newest effective dates first; empty result is returned as ``[]``.
        """
        params = [
            ("limit", str(max(1, limit))),
            ("conditions[0][property]", _COL_DESCRIPTION),
            ("conditions[0][operator]", "contains"),
            ("conditions[0][value]", drug.strip()),
            ("sorts[0][property]", _COL_EFFECTIVE),
            ("sorts[0][order]", "desc"),
        ]
        url = f"{_DATASTORE_QUERY}/{dataset_id}/0?{urlencode(params)}"
        payload = self.http.get_json(url)
        results = payload.get("results") if isinstance(payload, dict) else None
        return results if isinstance(results, list) else []

    def _to_document(
        self, company: str, dataset_id: str, drug: str, row: Dict[str, Any]
    ) -> Optional[Document]:
        description = str(row.get(_COL_DESCRIPTION, "") or "").strip()
        ndc = str(row.get("ndc", "") or "").strip()
        effective = str(row.get(_COL_EFFECTIVE, "") or "").strip()
        unit = str(row.get(_COL_UNIT, "") or "").strip()
        per_unit = _to_float(row.get(_COL_PER_UNIT))

        if not description and not ndc:
            return None  # nothing identifiable; skip

        doc_id = "-".join(p for p in (ndc, effective) if p) or description or drug

        price_str = f"{per_unit:.5f}" if per_unit is not None else "n/a"
        title = f"NADAC price: {description or ndc}"

        text_lines = [
            f"Drug: {description or 'n/a'}",
            f"NDC: {ndc or 'n/a'}",
            f"NADAC per unit: ${price_str}" if per_unit is not None
            else "NADAC per unit: n/a",
            f"Pricing unit: {unit or 'n/a'}",
            f"Effective date: {effective or 'n/a'}",
        ]
        classification = str(row.get("classification_for_rate_setting", "") or "").strip()
        if classification:
            text_lines.append(f"Rate-setting classification: {classification}")
        otc = str(row.get("otc", "") or "").strip()
        if otc:
            text_lines.append(f"OTC: {otc}")
        text = "\n".join(text_lines)

        return Document(
            company=company,
            source=self.source,
            doc_type="nadac_price",
            doc_id=doc_id,
            title=title,
            url=f"{_DATASET_PAGE}/{dataset_id}",
            published=effective or None,
            metadata={
                "ndc": ndc,
                "ndc_description": description,
                "nadac_per_unit": per_unit,
                "pricing_unit": unit,
                "effective_date": effective,
                "as_of_date": str(row.get("as_of_date", "") or "").strip(),
                "otc": otc,
                "classification_for_rate_setting": classification,
                "dataset_id": dataset_id,
                "drug_query": drug,
            },
            text=text,
            raw=json.dumps(row, indent=2, sort_keys=True),
        )


def _as_str_list(value: Any) -> List[str]:
    """Coerce the ``drugs`` option into a clean list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    out: List[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _to_float(value: Any) -> Optional[float]:
    """Parse NADAC per-unit strings like ``'0.01419'`` to float; None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _search_items(payload: Any) -> List[Dict[str, Any]]:
    """Normalize DKAN search ``results`` (a dict keyed by id) into a list."""
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if isinstance(results, dict):
        return [v for v in results.values() if isinstance(v, dict)]
    if isinstance(results, list):
        return [v for v in results if isinstance(v, dict)]
    return []
