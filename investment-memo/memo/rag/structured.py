"""Structured accessors over ingested data.

The most citable, highest-value facts arrive already structured (clinical-trial
fields, filing metadata), so they are queried by field here rather than retrieved
as fuzzy text. Each record keeps its source url so a downstream claim can cite it
directly. This sits alongside the text retriever, not inside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from memo.ingestion.base import Document, Storage


@dataclass
class TrialRecord:
    """A clinical trial reduced to the fields an analyst reasons over."""

    nct_id: str
    title: str
    url: str
    phases: List[str]
    status: Optional[str]
    conditions: List[str]
    interventions: List[str]
    enrollment: Optional[int]
    lead_sponsor: Optional[str]
    start_date: Optional[str]
    primary_completion_date: Optional[str]
    primary_outcomes: List[str]


@dataclass
class FilingRecord:
    """An EDGAR filing reduced to its index-level metadata."""

    accession: str
    form: str
    url: str
    filing_date: Optional[str]
    report_date: Optional[str]
    primary_document: Optional[str]


@dataclass
class PriceRecord:
    """A per-unit drug price reduced to the fields a net-price input reasons over.

    Sourced from a comparator drug (Kymera's own assets are investigational and have no
    price), so this anchors the peak-sales net-price parameter on a cited figure rather
    than an assumption. ``price_per_unit`` is per dosage/pricing unit, not per year;
    ``doc_id``/``doc_type``/``url`` carry the citation so a downstream claim can point
    straight at the CMS/NADAC document it came from.
    """

    drug: str  # brand name where known, else generic
    generic: Optional[str]
    manufacturer: Optional[str]
    price_per_unit: float
    unit: str  # the dosage/pricing unit the price is per
    period: Optional[str]  # data year (CMS) or effective date (NADAC)
    source: str  # "cms" or "nadac"
    doc_type: str  # "spending" or "nadac_price"
    doc_id: str
    url: str


class StructuredStore:
    """Field-level access to a company's ingested documents.

    Reads back the metadata records that ingestion wrote, and exposes them as typed
    records. Numeric XBRL facts (cash, shares) are a planned addition; today this
    covers clinical trials and the filing index.
    """

    def __init__(self, company: str, storage: Optional[Storage] = None) -> None:
        self.company = company
        self.storage = storage or Storage()

    def trials(self) -> List[TrialRecord]:
        records = []
        for doc in self.storage.load_documents(self.company, source="clinicaltrials"):
            records.append(self._to_trial(doc))
        return records

    def filings(self, forms: Optional[List[str]] = None) -> List[FilingRecord]:
        wanted = {f.upper() for f in forms} if forms else None
        records = []
        for doc in self.storage.load_documents(self.company, source="edgar"):
            if wanted and doc.doc_type.upper() not in wanted:
                continue
            records.append(self._to_filing(doc))
        return records

    def prices(self) -> List[PriceRecord]:
        """CMS Medicare Part D spending docs -> per-dosage-unit near-net PriceRecords.

        Reads ``avg_spending_per_dosage_unit`` (the realized weighted near-net price).
        Rows with no parseable price are skipped so every record carries a real float.
        """
        records = []
        for doc in self.storage.load_documents(self.company, source="cms"):
            if doc.doc_type != "spending":
                continue
            record = self._to_price(doc)
            if record is not None:
                records.append(record)
        return records

    def acquisition_costs(self) -> List[PriceRecord]:
        """NADAC docs -> pharmacy acquisition-cost PriceRecords (``nadac_per_unit``).

        Rows with no parseable price are skipped so every record carries a real float.
        """
        records = []
        for doc in self.storage.load_documents(self.company, source="nadac"):
            if doc.doc_type != "nadac_price":
                continue
            record = self._to_acquisition_cost(doc)
            if record is not None:
                records.append(record)
        return records

    # ---------------------------------------------------------------- internals

    @staticmethod
    def _to_trial(doc: Document) -> TrialRecord:
        m: Dict[str, Any] = doc.metadata or {}
        interventions = m.get("interventions") or []
        return TrialRecord(
            nct_id=doc.doc_id,
            title=doc.title,
            url=doc.url,
            phases=_as_list(m.get("phases")),
            status=m.get("overall_status") or m.get("status"),
            conditions=_as_list(m.get("conditions")),
            interventions=[_intervention_name(i) for i in interventions],
            enrollment=m.get("enrollment"),
            lead_sponsor=m.get("lead_sponsor"),
            start_date=m.get("start_date"),
            primary_completion_date=m.get("primary_completion_date"),
            primary_outcomes=_as_list(m.get("primary_outcomes")),
        )

    @staticmethod
    def _to_price(doc: Document) -> Optional["PriceRecord"]:
        m: Dict[str, Any] = doc.metadata or {}
        price = _to_float(m.get("avg_spending_per_dosage_unit"))
        if price is None:
            return None
        year = m.get("year")
        return PriceRecord(
            drug=m.get("brand_name") or m.get("generic_name") or doc.title,
            generic=m.get("generic_name") or None,
            manufacturer=m.get("manufacturer") or None,
            price_per_unit=price,
            unit="dosage unit",
            period=str(year) if year not in (None, "") else doc.published,
            source=doc.source,
            doc_type=doc.doc_type,
            doc_id=doc.doc_id,
            url=doc.url,
        )

    @staticmethod
    def _to_acquisition_cost(doc: Document) -> Optional["PriceRecord"]:
        m: Dict[str, Any] = doc.metadata or {}
        price = _to_float(m.get("nadac_per_unit"))
        if price is None:
            return None
        return PriceRecord(
            drug=m.get("ndc_description") or m.get("drug_query") or doc.title,
            generic=m.get("ndc_description") or None,
            manufacturer=None,
            price_per_unit=price,
            unit=m.get("pricing_unit") or "unit",
            period=m.get("effective_date") or doc.published,
            source=doc.source,
            doc_type=doc.doc_type,
            doc_id=doc.doc_id,
            url=doc.url,
        )

    @staticmethod
    def _to_filing(doc: Document) -> FilingRecord:
        m: Dict[str, Any] = doc.metadata or {}
        return FilingRecord(
            accession=m.get("accession") or doc.doc_id,
            form=doc.doc_type,
            url=doc.url,
            filing_date=doc.published or m.get("filing_date"),
            report_date=m.get("report_date"),
            primary_document=m.get("primary_document"),
        )


def _to_float(value: Any) -> Optional[float]:
    """Cast a metadata numeric to float defensively; None when it isn't a number.

    Prices may arrive as floats from the ingester or as strings once reloaded from a
    JSON record, so string numerics (``"12.50"``, ``"$34.56"``, ``"1,234"``) are coerced
    and empty/suppressed markers become None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if text == "" or text.lower() in ("na", "n/a", "null", "none", "*", "."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _intervention_name(item: Any) -> str:
    """Interventions may be stored as strings or {name, type} dicts; normalize to a name."""
    if isinstance(item, dict):
        name = item.get("name", "")
        kind = item.get("type")
        return f"{name} ({kind})" if kind else str(name)
    return str(item)
