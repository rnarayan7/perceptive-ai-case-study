"""CMS Medicare Part D Spending by Drug ingestion (data.cms.gov data-api).

Realized, near-net price per dosage unit for ANALOG / comparator drugs. Kymera's
assets are investigational and are not in CMS, so this source never describes KYMR's
own pipeline; it supplies the net-price backbone for the peak-sales model by pulling
cited spending figures for named comparators (e.g. Dupixent / dupilumab for the AD /
asthma anchor, or the HS biologics).

The Part D "Spending by Drug" file is a *wide* table: one row per drug (optionally
split by manufacturer, with an aggregate ``Overall`` row), and metrics carried in
year-suffixed columns (``Tot_Spndng_2023``, ``Avg_Spnd_Per_Dsg_Unt_Wghtd_2023`` ...).
We match the requested drug names client-side and pivot each matched row into one
Document per data year, so a year is a citable unit.

Keyless JSON, paged; no third-party dependencies. All network goes through
``self.http`` (stdlib, keyless GET).

Endpoint (verified 2026-09-07, feasibility source #5)::

    https://data.cms.gov/data-api/v1/dataset/{dataset_id}/data

Fields read for net price (year ``Y`` suffix):
    Brnd_Name, Gnrc_Name, Mftr_Name,
    Tot_Spndng_Y, Tot_Dsg_Unts_Y, Tot_Clms_Y, Tot_Benes_Y,
    Avg_Spnd_Per_Dsg_Unt_Wghtd_Y  <- the realized near-net price per dosage unit,
    Avg_Spnd_Per_Clm_Y, Avg_Spnd_Per_Bene_Y.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from memo.ingestion.base import BaseIngester, Document

logger = logging.getLogger("memo.ingestion")

# Data years live in column suffixes; we discover them from the row rather than
# hard-coding a range, so a new annual release just works.
_YEAR_COLUMN = re.compile(r"^Tot_Spndng_(\d{4})$")


class CmsSpendingIngester(BaseIngester):
    """Medicare Part D Spending by Drug -> per-drug, per-year spending Documents.

    ``fetch(company, drugs=[...])`` looks up each comparator name in the CMS file and
    returns one :class:`Document` per matched drug and data year. KYMR itself has no
    marketed drug in CMS, so with no ``drugs`` the correct result is an empty list.
    """

    source = "cms"

    # Medicare Part D Spending by Drug (release year 2026, data year 2024).
    # data.cms.gov dataset id verified reachable keyless on 2026-09-07. Dataset ids
    # rotate per annual release, so callers may override via the ``dataset_id`` option.
    DEFAULT_DATASET_ID = "7e0b4365-fd63-4a29-8f5e-e0ac9f66a81b"
    BASE_URL = "https://data.cms.gov/data-api/v1/dataset"
    LANDING_URL = (
        "https://data.cms.gov/summary-statistics-on-use-and-payments/"
        "medicare-medicaid-spending-by-drug/medicare-part-d-spending-by-drug"
    )

    PAGE_SIZE = 5000  # data-api caps a page at 5,000 rows
    MAX_PAGES = 10  # safety cap; a keyword-filtered lookup returns far fewer

    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Return spending Documents for the comparator drugs in ``options['drugs']``.

        Options:
            drugs: list of brand and/or generic names to look up
                   (e.g. ["Dupixent", "dupilumab"]). A bare string is accepted too.
            dataset_id: override the CMS dataset id (releases rotate the id).

        Empty result is CORRECT and never raises: KYMR has no marketed drug in CMS, so
        callers that pass no ``drugs`` get [] plus a logged note.
        """
        drugs = options.get("drugs") or []
        if isinstance(drugs, str):
            drugs = [drugs]
        drugs = [str(d).strip() for d in drugs if str(d).strip()]

        dataset_id = str(options.get("dataset_id") or self.DEFAULT_DATASET_ID)

        if not drugs:
            logger.info(
                "cms: no 'drugs' comparator names given for %s; returning [] "
                "(KYMR's investigational assets are not in CMS - pass drugs=[...] with "
                "comparator brand/generic names, e.g. ['Dupixent','dupilumab'])",
                company,
            )
            return []

        documents: List[Document] = []
        seen: set = set()
        for name in drugs:
            try:
                rows = self._lookup(dataset_id, name)
            except Exception:  # noqa: BLE001 - one bad drug lookup should not abort others
                logger.exception("cms: lookup failed for %r", name)
                raise
            for row in self._matching_rows(rows, name):
                for document in self._row_to_year_documents(company, name, row):
                    if document.doc_id in seen:
                        continue
                    seen.add(document.doc_id)
                    documents.append(document)
        logger.info("cms: %d spending rows for %s across %d drug name(s)",
                    len(documents), company, len(drugs))
        return documents

    # ---------------------------------------------------------------- internals

    def _lookup(self, dataset_id: str, name: str) -> List[Dict[str, Any]]:
        """Page the dataset with a server-side keyword filter for ``name``.

        ``keyword`` does a full-text match across columns, so the result set is small;
        we still confirm the match client-side in :meth:`_matching_rows`.
        """
        rows: List[Dict[str, Any]] = []
        offset = 0
        for _ in range(self.MAX_PAGES):
            params = urlencode({"keyword": name, "size": self.PAGE_SIZE, "offset": offset})
            url = f"{self.BASE_URL}/{dataset_id}/data?{params}"
            page = self.http.get_json(url)
            if not isinstance(page, list) or not page:
                break
            rows.extend(page)
            if len(page) < self.PAGE_SIZE:
                break
            offset += self.PAGE_SIZE
        return rows

    def _matching_rows(self, rows: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
        """Keep rows whose brand or generic name matches ``name`` (case-insensitive).

        Prefer the aggregate ``Overall`` manufacturer row when present: it carries
        whole-program spend and the weighted net price across all manufacturers, which
        is the analog figure we want. Fall back to per-manufacturer rows otherwise.
        """
        key = name.strip().lower()
        matched: List[Dict[str, Any]] = []
        for row in rows:
            brand = (_get(row, "Brnd_Name") or "").strip().lower()
            generic = (_get(row, "Gnrc_Name") or "").strip().lower()
            if not key:
                continue
            if key == brand or key == generic or key in brand or key in generic:
                matched.append(row)

        overall = [r for r in matched
                   if (_get(r, "Mftr_Name") or "").strip().lower() == "overall"]
        return overall or matched

    def _row_to_year_documents(
        self, company: str, query: str, row: Dict[str, Any]
    ) -> List[Document]:
        """Pivot one wide drug row into one Document per data year with spending."""
        brand = (_get(row, "Brnd_Name") or "").strip()
        generic = (_get(row, "Gnrc_Name") or "").strip()
        manufacturer = (_get(row, "Mftr_Name") or "").strip()
        label = brand or generic or query

        years = sorted({m.group(1) for k in row for m in [_YEAR_COLUMN.match(k)] if m})
        documents: List[Document] = []
        for year in years:
            total_spending = _num(_get(row, f"Tot_Spndng_{year}"))
            if total_spending is None:
                continue  # drug not marketed / value suppressed that year

            avg_per_dosage_unit = _num(_get(row, f"Avg_Spnd_Per_Dsg_Unt_Wghtd_{year}"))
            avg_per_claim = _num(_get(row, f"Avg_Spnd_Per_Clm_{year}"))
            avg_per_bene = _num(_get(row, f"Avg_Spnd_Per_Bene_{year}"))
            total_claims = _num(_get(row, f"Tot_Clms_{year}"))
            total_benes = _num(_get(row, f"Tot_Benes_{year}"))
            total_units = _num(_get(row, f"Tot_Dsg_Unts_{year}"))

            documents.append(Document(
                company=company,
                source=self.source,
                doc_type="spending",
                doc_id=self._doc_id(label, manufacturer, year),
                title=self._title(brand, generic, manufacturer, year),
                url=self._source_url(brand or generic or query),
                published=year,
                metadata={
                    "brand_name": brand,
                    "generic_name": generic,
                    "manufacturer": manufacturer,
                    "year": year,
                    "query": query,
                    "total_spending": total_spending,
                    "total_claims": total_claims,
                    "total_beneficiaries": total_benes,
                    "total_dosage_units": total_units,
                    "avg_spending_per_dosage_unit": avg_per_dosage_unit,
                    "avg_spending_per_claim": avg_per_claim,
                    "avg_spending_per_beneficiary": avg_per_bene,
                },
                text=self._summary_text(
                    brand, generic, manufacturer, year, total_spending, total_claims,
                    total_benes, total_units, avg_per_dosage_unit, avg_per_claim,
                    avg_per_bene,
                ),
                raw=json.dumps(row, ensure_ascii=False),
            ))
        return documents

    # ------------------------------------------------------------ formatting

    @staticmethod
    def _doc_id(label: str, manufacturer: str, year: str) -> str:
        # Aggregate "Overall" rows collapse to drug+year; per-manufacturer rows keep
        # the manufacturer so distinct rows do not overwrite each other on disk.
        if manufacturer and manufacturer.lower() != "overall":
            return f"{label}_{manufacturer}_{year}"
        return f"{label}_{year}"

    @staticmethod
    def _title(brand: str, generic: str, manufacturer: str, year: str) -> str:
        name = brand or generic or "Unknown drug"
        if brand and generic and brand.lower() != generic.lower():
            name = f"{brand} ({generic})"
        title = f"Medicare Part D spending: {name}, {year}"
        if manufacturer and manufacturer.lower() != "overall":
            title += f" - {manufacturer}"
        return title

    def _source_url(self, drug: str) -> str:
        return f"{self.LANDING_URL}?keyword={drug}"

    @staticmethod
    def _summary_text(
        brand: str, generic: str, manufacturer: str, year: str,
        total_spending: Optional[float], total_claims: Optional[float],
        total_benes: Optional[float], total_units: Optional[float],
        avg_per_dosage_unit: Optional[float], avg_per_claim: Optional[float],
        avg_per_bene: Optional[float],
    ) -> str:
        name = brand or generic or "Unknown drug"
        if brand and generic and brand.lower() != generic.lower():
            name = f"{brand} ({generic})"
        lines = [f"{name} - Medicare Part D spending, {year}."]
        if manufacturer:
            lines.append(f"Manufacturer: {manufacturer}.")
        lines.append(f"Total spending: {_money(total_spending)}.")
        if total_claims is not None:
            lines.append(f"Total claims: {_count(total_claims)}.")
        if total_benes is not None:
            lines.append(f"Total beneficiaries: {_count(total_benes)}.")
        if total_units is not None:
            lines.append(f"Total dosage units: {_count(total_units)}.")
        if avg_per_dosage_unit is not None:
            lines.append(
                "Average spending per dosage unit (weighted, a realized near-net "
                f"price): {_money(avg_per_dosage_unit)}.")
        if avg_per_claim is not None:
            lines.append(f"Average spending per claim: {_money(avg_per_claim)}.")
        if avg_per_bene is not None:
            lines.append(f"Average spending per beneficiary: {_money(avg_per_bene)}.")
        return "\n".join(lines)


# ---------------------------------------------------------------- helpers

def _get(row: Dict[str, Any], key: str) -> Any:
    """Fetch ``key`` from a row, tolerating case differences in the API's headers."""
    if key in row:
        return row[key]
    lowered = key.lower()
    for existing, value in row.items():
        if existing.lower() == lowered:
            return value
    return None


def _num(value: Any) -> Optional[float]:
    """Parse a CMS numeric cell to float; None for empty or suppressed values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if text == "" or text in ("*", ".", "NA", "N/A", "null", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value >= 100:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def _count(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f}"
