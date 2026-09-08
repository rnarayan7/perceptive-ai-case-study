"""CDC epidemiology ingestion via the data.cdc.gov Socrata (SODA) API.

Peak-sales models need a defensible addressable population: how many people in the
US actually have the condition a drug treats. data.cdc.gov exposes CDC's surveillance
series (NHANES/NHIS/BRFSS-derived prevalence, chronic-disease indicators) as keyless
JSON through the Socrata SODA API, queryable with SoQL params (``$where``, ``$select``,
``$limit``). One GET per run, ``json.loads``, one Document per row.

The default dataset is the U.S. Chronic Disease Indicators (CDI) series, filtered to the
Asthma topic, which anchors the KT-621 (STAT6 degrader, asthma) denominator. Any other
CDC Socrata dataset works by passing ``dataset=<resource id>``.

Honest scope note (see docs/source-ingestion-feasibility.md, source 10): hidradenitis
suppurativa and adult atopic-dermatitis prevalence are not clean CDC series and come from
the claims-database literature, not this API. This source covers the asthma / chronic-
disease denominators CDC does publish.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from memo.ingestion.base import BaseIngester, Document

# Verified keyless Socrata host (checked 2026-09-07, feasibility report source 10).
_HOST = "https://data.cdc.gov"

# U.S. Chronic Disease Indicators (CDI). Keyless JSON, includes an "Asthma" topic with
# BRFSS-derived "Current asthma among adults" crude/age-adjusted prevalence by state,
# year, and demographic stratification. Verified live 2026-09-07.
_DEFAULT_DATASET = "hksd-2xuw"

# Applied only for the default dataset when no where/query is passed, to keep the default
# run relevant to KYMR's respiratory/derm space rather than dumping every indicator.
_DEFAULT_WHERE = "topic='Asthma'"

_DEFAULT_LIMIT = 50


class CdcIngester(BaseIngester):
    """Ingest CDC epidemiology rows from a data.cdc.gov Socrata dataset.

    ``fetch`` options:

    - ``dataset``: Socrata resource id (e.g. ``"hksd-2xuw"``). Defaults to the U.S.
      Chronic Disease Indicators series.
    - ``where``: a SoQL ``$where`` filter (e.g. ``"topic='Asthma' AND datavalue IS NOT NULL"``).
    - ``query``: a SoQL ``$q`` full-text search (alternative to ``where``).
    - ``select``: a SoQL ``$select`` projection (optional).
    - ``order``: a SoQL ``$order`` clause (optional).
    - ``limit``: max rows to fetch (default 50).

    One :class:`Document` (``doc_type="epi"``) per returned row. An empty result set is
    returned as an empty list, never an error.
    """

    source = "cdc"

    def fetch(self, company: str, **options: Any) -> List[Document]:
        dataset = str(options.get("dataset") or _DEFAULT_DATASET).strip()
        if not dataset:
            # Only reachable if the default is ever cleared; give a clear message.
            raise ValueError("no CDC dataset available: pass dataset=<socrata resource id>")

        where = options.get("where")
        query = options.get("query")
        select = options.get("select")
        order = options.get("order")
        limit = int(options.get("limit", _DEFAULT_LIMIT))

        # Keep the default run KYMR-relevant when nothing was asked for explicitly.
        if where is None and query is None and dataset == _DEFAULT_DATASET:
            where = _DEFAULT_WHERE

        params: Dict[str, Any] = {"$limit": limit}
        if where:
            params["$where"] = where
        if query:
            params["$q"] = query
        if select:
            params["$select"] = select
        if order:
            params["$order"] = order

        url = f"{_HOST}/resource/{dataset}.json?{urlencode(params)}"
        rows = self.http.get_json(url)
        if not isinstance(rows, list):
            # A Socrata error payload is a dict, not a list; treat as empty rather than raise.
            return []

        landing = f"{_HOST}/d/{dataset}"
        documents: List[Document] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            documents.append(self._to_document(company, dataset, landing, row, index))
        return documents

    # ---------------------------------------------------------------- internals

    def _to_document(
        self,
        company: str,
        dataset: str,
        landing: str,
        row: Dict[str, Any],
        index: int,
    ) -> Document:
        raw = json.dumps(row, sort_keys=True)
        doc_id = f"{dataset}-{self._row_key(row, raw, index)}"

        topic = _first(row, "topic", "question")
        location = _first(row, "locationdesc", "locationabbr")
        measure = _first(row, "question", "topic", "datavaluetype")
        year = self._year(row)

        title = " - ".join(p for p in (topic, location, year) if p) or f"CDC row {doc_id}"

        return Document(
            company=company,
            source=self.source,
            doc_type="epi",
            doc_id=doc_id,
            title=title,
            url=landing,
            published=(row.get("yearend") or row.get("yearstart") or None),
            metadata={
                "dataset": dataset,
                "topic": row.get("topic"),
                "question": row.get("question"),
                "location": location,
                "location_abbr": row.get("locationabbr"),
                "data_source": row.get("datasource"),
                "value_type": row.get("datavaluetype"),
                "value_unit": row.get("datavalueunit"),
                "value": row.get("datavalue"),
                "year_start": row.get("yearstart"),
                "year_end": row.get("yearend"),
                "stratification_category": row.get("stratificationcategory1"),
                "stratification": row.get("stratification1"),
            },
            text=self._summary(row, measure, location, year),
            raw=raw,
        )

    @staticmethod
    def _summary(row: Dict[str, Any], measure: str, location: str, year: str) -> str:
        """Readable one/few-line rendering of the row's measure, value, place, and year."""
        value = row.get("datavalue")
        unit = (row.get("datavalueunit") or "").strip()
        value_type = (row.get("datavaluetype") or "").strip()

        lead = measure or row.get("topic") or "CDC indicator"
        parts: List[str] = [lead]
        if location:
            parts.append(f"in {location}")
        if year:
            parts.append(f"({year})")

        line = " ".join(parts) + ":"
        if value not in (None, ""):
            rendered = f"{value}{unit}" if unit and unit != "Number" else str(value)
            if value_type:
                rendered = f"{rendered} ({value_type})"
            line = f"{line} {rendered}"
        else:
            line = f"{line} value not reported"

        strat_cat = (row.get("stratificationcategory1") or "").strip()
        strat = (row.get("stratification1") or "").strip()
        if strat:
            line = f"{line} [{strat_cat + ': ' if strat_cat else ''}{strat}]"

        source = (row.get("datasource") or "").strip()
        if source:
            line = f"{line} Source: {source}."
        return line.strip()

    @staticmethod
    def _year(row: Dict[str, Any]) -> str:
        start = (row.get("yearstart") or "").strip()
        end = (row.get("yearend") or "").strip()
        if start and end and start != end:
            return f"{start}-{end}"
        return start or end

    @staticmethod
    def _row_key(row: Dict[str, Any], raw: str, index: int) -> str:
        """Stable per-row key.

        Prefer Socrata's row id if present (``:id``), then a composite of the meaningful
        identifying columns, else a hash of the row so re-runs stay deterministic.
        """
        row_id = row.get(":id")
        if row_id:
            return str(row_id)

        composite = "_".join(
            str(row.get(field, "")).strip()
            for field in (
                "locationabbr",
                "questionid",
                "datavaluetypeid",
                "yearstart",
                "yearend",
                "stratificationid1",
            )
        ).strip("_")
        if composite.replace("_", ""):
            return composite

        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _first(row: Dict[str, Any], *fields: str) -> str:
    """First non-empty string value among ``fields`` (safe lookups)."""
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            return str(value).strip()
    return ""
