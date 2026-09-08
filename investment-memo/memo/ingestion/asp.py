"""Medicare Part B ASP (Average Sales Price) drug-pricing ingester.

Where :mod:`memo.ingestion.nadac` gives the pharmacy-acquisition (generic-floor)
price for self-administered / pharmacy-dispensed drugs, this source gives the
*branded, net-of-rebate* price for **physician-administered** drugs: the infused
and injected biologics billed under Medicare Part B. CMS publishes a Part B
Payment Limit for each such drug every quarter, set at ``ASP x 1.06`` (ASP plus a
6% add-on), so the underlying average sales price per billing unit is recoverable
as ``Payment Limit / 1.06``.

For a pre-commercial pipeline, ASP is the best free net-price anchor for the
marketed *comparator* biologics a peak-sales model leans on (e.g. Immunovant's
anti-FcRn assets vs. efgartigimod/Vyvgart, or Kymera's STAT6/IRAK4 degraders vs.
the asthma/AD biologics). Oral comparators are billed under Part D, not Part B,
and are correctly absent here; they are reported as absent, never substituted.

Flow:

1. Fetch the ASP pricing-files landing page and pick the most recent quarter's
   Payment Limit file link (files are named like
   ``july-2026-medicare-part-b-payment-limit-files.zip``).
2. Download it (a zip, or occasionally a bare CSV), read the section-508 CSV of
   the Payment Limit file, and build a table keyed by HCPCS J-code + short
   description.
3. For each comparator drug term, case-insensitive substring-match the short
   description and emit one :class:`Document` per matched HCPCS row, carrying the
   computed ASP per billing unit, the payment limit, the dosage descriptor, and
   the effective quarter.

Only the standard library is used (``urllib``, ``csv``, ``zipfile``, ``io``). All
network access goes through ``self.http`` (the shared :class:`HttpClient`), which
sets the descriptive User-Agent and rate-limits.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .base import BaseIngester, Document, HttpClient, Storage

logger = logging.getLogger("memo.ingestion")

# CMS host (data.cms.gov is unreachable from here; the www host serves the files).
CMS_BASE = "https://www.cms.gov"
ASP_FILES_PAGE = "https://www.cms.gov/medicare/payment/part-b-drugs/asp-pricing-files"

# The Part B add-on: payment limit = ASP x 1.06, so ASP = payment limit / 1.06.
ASP_MARKUP = 1.06

# Month name -> (month number, calendar quarter). ASP files are quarterly and the
# filename leads with the quarter's first month.
_MONTHS: Dict[str, Tuple[int, int]] = {
    "january": (1, 1),
    "april": (4, 2),
    "july": (7, 3),
    "october": (10, 4),
}

# A pricing-file link carries a month, a year, and one of these tokens. The
# naming changed over time: older quarters say "asp-pricing-file", recent ones
# say "medicare-part-b-payment-limit-file(s)". Either is the file we want.
_PRICING_TOKENS = ("asp-pricing-file", "payment-limit-file")
# Links to exclude even when they carry a pricing token: the NOC (not-otherwise-
# classified) file, the NDC->HCPCS crosswalk, vaccine files, and the companion
# "drugs not payable under Part B" file.
_EXCLUDE_TOKENS = ("crosswalk", "noc-pricing", "not-payable", "not payable",
                   "vaccine", "ndc-hcpcs", "seasonal")

_HREF_MONTH_YEAR = re.compile(
    r"(january|april|july|october)[-_ ]?(\d{4})", re.IGNORECASE
)
# "Effective July 1, 2026 through September 30, 2026" header inside the CSV.
_EFFECTIVE_RANGE = re.compile(
    r"Effective\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})\s+through\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
# "based on 1Q26 ASP data" provenance note.
_ASP_DATA_NOTE = re.compile(r"based on\s+([0-9]Q[0-9]{2})\s+ASP data", re.IGNORECASE)

# CSV column headers on the Payment Limit file (verified July 2026 file).
_COL_HCPCS = "hcpcs code"
_COL_DESC = "short description"
_COL_DOSAGE = "hcpcs code dosage"
_COL_LIMIT = "payment limit"
_COL_COINS = "co-insurance percentage"


# Curated per-company comparator drug terms, derived from each company's own
# clinicaltrials / pubmed / openfda corpus. Terms are matched case-insensitively
# as substrings of the ASP short description, so a generic stem suffices. Only
# physician-administered (Part B) comparators land here; oral comparators (Part D)
# are intentionally omitted and reported as legitimately absent. Overridable per
# run via the ``drugs=[...]`` option.
COMPARATORS: Dict[str, Sequence[str]] = {
    # Abivax / obefazimod (ulcerative colitis). Infused UC biologics.
    "ABVX": ("infliximab", "vedolizumab", "ustekinumab", "golimumab"),
    # Kymera STAT6 / IRAK4 degraders (asthma, atopic dermatitis). Provider-
    # administered respiratory/allergy biologics.
    "KYMR": ("tezepelumab", "mepolizumab", "benralizumab", "omalizumab"),
    # Immunovant anti-FcRn (batoclimab, IMVT-1402) for autoimmune disease.
    "IMVT": ("efgartigimod", "eculizumab", "ravulizumab", "rituximab"),
    # Praxis: oral CNS small molecules (epilepsy, movement disorders, depression).
    # Comparators are oral -> Part D, so none are expected in ASP.
    "PRAX": (),
    # Cogent bezuclastinib (systemic mastocytosis, GIST). The on-target
    # comparators are oral TKIs (avapritinib, midostaurin, imatinib, sunitinib,
    # ripretinib) -> Part D, so none are expected in ASP.
    "COGT": (),
}


@dataclass(frozen=True)
class AspFile:
    """A resolved ASP pricing file: its download URL and parsed quarter."""

    url: str
    filename: str
    month: int
    year: int
    quarter: int  # calendar quarter 1..4

    @property
    def quarter_label(self) -> str:
        return f"{self.year} Q{self.quarter}"


@dataclass(frozen=True)
class AspRecord:
    """One parsed HCPCS pricing row from the Payment Limit file."""

    hcpcs: str
    description: str
    dosage: str
    payment_limit: float
    coinsurance: Optional[float]

    @property
    def asp_per_unit(self) -> float:
        """ASP per billing unit = payment limit / 1.06."""
        return self.payment_limit / ASP_MARKUP


@dataclass(frozen=True)
class AspTable:
    """The parsed pricing file: its records plus the effective window."""

    records: List[AspRecord]
    effective_start: Optional[str]  # ISO YYYY-MM-DD
    effective_end: Optional[str]
    asp_data_note: str  # e.g. "1Q26"
    source: AspFile


class _LinkExtractor(HTMLParser):
    """Collects every anchor ``href`` on a page (stdlib, like the EDGAR parser)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


class AspIngester(BaseIngester):
    """Ingest Medicare Part B ASP net prices for comparator biologics.

    Usage::

        manifest = AspIngester().run("IMVT")                     # curated comparators
        manifest = AspIngester().run("IMVT", drugs=["efgartigimod"])

    ``fetch`` options:

    * ``drugs`` (list[str]): drug terms to substring-match against the HCPCS short
      description. Defaults to the curated :data:`COMPARATORS` for the ticker; an
      empty list (e.g. an oral-only pipeline) fetches nothing and logs why.
    * ``url`` (str): pin a specific pricing-file URL, bypassing page resolution.
    """

    source = "asp"

    def __init__(
        self,
        http: Optional[HttpClient] = None,
        storage: Optional[Storage] = None,
    ) -> None:
        super().__init__(http=http, storage=storage)
        # Cache the parsed table per resolved URL so a multi-company run downloads
        # and parses each quarterly file only once.
        self._table_cache: Dict[str, AspTable] = {}

    # ------------------------------------------------------------------ public

    def fetch(self, company: str, **options: Any) -> List[Document]:
        ticker = company.strip().upper()
        drugs = _as_str_list(options.get("drugs"))
        if options.get("drugs") is None:
            drugs = [str(d) for d in COMPARATORS.get(ticker, ())]

        if not drugs:
            logger.info(
                "asp: no comparator drugs for %s (its comparators are oral / Part D, "
                "not Part B ASP); nothing to fetch.",
                ticker,
            )
            return []

        pinned = options.get("url")
        table = self._load_table(pinned)

        documents: List[Document] = []
        seen: set = set()
        for drug in drugs:
            term = drug.strip().lower()
            if not term:
                continue
            for record in table.records:
                if term not in record.description.lower():
                    continue
                document = self._to_document(ticker, drug, record, table)
                if document.doc_id in seen:
                    continue
                seen.add(document.doc_id)
                documents.append(document)
        return documents

    # ---------------------------------------------------------------- internals

    def _load_table(self, pinned_url: Optional[str]) -> AspTable:
        """Resolve, download, and parse the pricing file (cached per URL)."""
        asp_file = (
            self._pinned_file(pinned_url)
            if pinned_url
            else self._resolve_latest_file()
        )
        if asp_file.url in self._table_cache:
            return self._table_cache[asp_file.url]
        table = self._download_and_parse(asp_file)
        self._table_cache[asp_file.url] = table
        return table

    def _resolve_latest_file(self) -> AspFile:
        """Pick the most recent quarter's Payment Limit file from the landing page."""
        html = self.http.get_text(ASP_FILES_PAGE)
        extractor = _LinkExtractor()
        extractor.feed(html)
        extractor.close()

        candidates: List[AspFile] = []
        for href in extractor.hrefs:
            low = href.lower()
            if not any(tok in low for tok in _PRICING_TOKENS):
                continue
            if any(tok in low for tok in _EXCLUDE_TOKENS):
                continue
            match = _HREF_MONTH_YEAR.search(low)
            if not match:
                continue
            month_name = match.group(1).lower()
            year = int(match.group(2))
            month, quarter = _MONTHS[month_name]
            url = href if href.startswith("http") else CMS_BASE + href
            candidates.append(
                AspFile(
                    url=url,
                    filename=href.rsplit("/", 1)[-1],
                    month=month,
                    year=year,
                    quarter=quarter,
                )
            )
        if not candidates:
            raise RuntimeError(
                "asp: no Part B pricing-file link found on the ASP pricing-files page"
            )
        latest = max(candidates, key=lambda f: (f.year, f.month))
        logger.info("asp: resolved latest pricing file %s (%s)",
                    latest.filename, latest.quarter_label)
        return latest

    @staticmethod
    def _pinned_file(url: str) -> AspFile:
        """Wrap a pinned URL, parsing its quarter from the filename when possible."""
        filename = url.rsplit("/", 1)[-1]
        match = _HREF_MONTH_YEAR.search(url.lower())
        if match:
            month, quarter = _MONTHS[match.group(1).lower()]
            year = int(match.group(2))
        else:
            month = quarter = 0
            year = 0
        return AspFile(url=url, filename=filename, month=month, year=year, quarter=quarter)

    def _download_and_parse(self, asp_file: AspFile) -> AspTable:
        """Download the file (zip or bare CSV) and parse the Payment Limit CSV."""
        data = self.http.get_bytes(asp_file.url)
        csv_text = self._extract_csv_text(data, asp_file)
        return parse_pricing_csv(csv_text, asp_file)

    @staticmethod
    def _extract_csv_text(data: bytes, asp_file: AspFile) -> str:
        """Return the Payment Limit CSV text from a zip payload or a bare CSV."""
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                member = _select_csv_member(archive.namelist())
                if member is None:
                    raise RuntimeError(
                        f"asp: no Payment Limit CSV in zip {asp_file.filename} "
                        f"(members: {archive.namelist()})"
                    )
                raw = archive.read(member)
            return raw.decode("latin-1")
        # Bare CSV (older quarters occasionally publish an unzipped file).
        return data.decode("latin-1")

    # ------------------------------------------------------------ doc assembly

    def _to_document(
        self, ticker: str, drug: str, record: AspRecord, table: AspTable
    ) -> Document:
        asp = record.asp_per_unit
        quarter = table.source.quarter_label
        effective = table.effective_start or ""

        metadata: Dict[str, Any] = {
            "hcpcs": record.hcpcs,
            "short_description": record.description,
            "dosage": record.dosage,
            "payment_limit": record.payment_limit,
            "asp_per_unit": asp,
            "asp_markup": ASP_MARKUP,
            "coinsurance_percentage": record.coinsurance,
            "effective_start": table.effective_start,
            "effective_end": table.effective_end,
            "quarter": quarter,
            "asp_data_note": table.asp_data_note,
            "drug_query": drug,
            "source_file": table.source.filename,
            "source_url": table.source.url,
            "pricing_basis": "Medicare Part B ASP (payment limit / 1.06)",
        }
        text = self._record_text(drug, record, table)
        return Document(
            company=ticker,
            source=self.source,
            doc_type="asp_price",
            doc_id=f"{record.hcpcs}_{effective or quarter}",
            title=f"Medicare Part B ASP: {record.description} ({record.hcpcs}, {quarter})",
            url=table.source.url,
            published=table.effective_start,
            metadata=metadata,
            text=text,
            raw=json.dumps(
                {
                    "hcpcs": record.hcpcs,
                    "short_description": record.description,
                    "dosage": record.dosage,
                    "payment_limit": record.payment_limit,
                    "asp_per_unit": asp,
                    "coinsurance_percentage": record.coinsurance,
                    "effective_start": table.effective_start,
                    "effective_end": table.effective_end,
                    "quarter": quarter,
                    "asp_data_note": table.asp_data_note,
                    "source_file": table.source.filename,
                },
                sort_keys=True,
            ),
        )

    @staticmethod
    def _record_text(drug: str, record: AspRecord, table: AspTable) -> str:
        """Searchable sentence naming the drug and the ASP net price for BM25.

        Includes the phrases "ASP", "average sales price", and "net price" so a
        keyword query for any of them retrieves this record.
        """
        dosage = record.dosage or "billing unit"
        when = (
            f"effective {table.source.quarter_label}"
            + (
                f" ({table.effective_start} through {table.effective_end})"
                if table.effective_start and table.effective_end
                else ""
            )
        )
        provenance = (
            f" This payment limit is based on {table.asp_data_note} ASP data."
            if table.asp_data_note
            else ""
        )
        return (
            f"Medicare Part B average sales price (ASP) for {drug} "
            f"({record.description}, HCPCS {record.hcpcs}). "
            f"ASP net price is ${record.asp_per_unit:,.4f} per {dosage} billing unit, "
            f"derived from the Part B payment limit of ${record.payment_limit:,.4f} "
            f"(ASP plus the 6% add-on), {when}. "
            f"ASP is the net-of-rebate average sales price and serves as the branded "
            f"net-price benchmark for this physician-administered drug.{provenance}"
        )


# ------------------------------------------------------------------- parsing


def parse_pricing_csv(csv_text: str, asp_file: AspFile) -> AspTable:
    """Parse a Part B Payment Limit CSV into an :class:`AspTable`.

    The file leads with several title / note lines, then a header row beginning
    ``HCPCS Code,Short Description,HCPCS Code Dosage,Payment Limit,...``, then the
    data rows. The effective window and the ASP-data-quarter note are pulled from
    the preamble. Rows without a numeric payment limit are skipped (never
    fabricated).
    """
    effective_start: Optional[str] = None
    effective_end: Optional[str] = None
    asp_note = ""
    range_match = _EFFECTIVE_RANGE.search(csv_text)
    if range_match:
        effective_start = _parse_us_date(range_match.group(1))
        effective_end = _parse_us_date(range_match.group(2))
    note_match = _ASP_DATA_NOTE.search(csv_text)
    if note_match:
        asp_note = note_match.group(1).upper()

    reader = csv.reader(io.StringIO(csv_text))
    header_index: Optional[Dict[str, int]] = None
    records: List[AspRecord] = []
    for row in reader:
        if not row:
            continue
        if header_index is None:
            if row[0].strip().lower() == _COL_HCPCS:
                header_index = _index_header(row)
            continue
        record = _row_to_record(row, header_index)
        if record is not None:
            records.append(record)

    if header_index is None:
        raise RuntimeError("asp: could not find the HCPCS header row in the pricing CSV")

    return AspTable(
        records=records,
        effective_start=effective_start,
        effective_end=effective_end,
        asp_data_note=asp_note,
        source=asp_file,
    )


def _index_header(row: Sequence[str]) -> Dict[str, int]:
    """Map normalized column names to their index (robust to column order)."""
    return {cell.strip().lower(): i for i, cell in enumerate(row)}


def _row_to_record(
    row: Sequence[str], header: Dict[str, int]
) -> Optional[AspRecord]:
    """Build an :class:`AspRecord` from a data row, or None if it is unusable."""
    hcpcs = _cell(row, header, _COL_HCPCS)
    description = _cell(row, header, _COL_DESC)
    dosage = _cell(row, header, _COL_DOSAGE)
    limit = _to_float(_cell(row, header, _COL_LIMIT))
    coins = _to_float(_cell(row, header, _COL_COINS))
    if not hcpcs or not description or limit is None:
        return None  # not a priced drug row (e.g. a footnote or blank line)
    return AspRecord(
        hcpcs=hcpcs,
        description=description,
        dosage=dosage,
        payment_limit=limit,
        coinsurance=coins,
    )


def _select_csv_member(names: Sequence[str]) -> Optional[str]:
    """Pick the Payment Limit / ASP Pricing CSV member from a zip's namelist.

    Prefers the section-508 CSV of the pricing file, excluding the companion
    "Drugs Not Payable" (NOC) file and any crosswalk.
    """
    csvs = [n for n in names if n.lower().endswith(".csv")]
    priced = [
        n for n in csvs
        if ("payment limit" in n.lower() or "asp pricing" in n.lower())
        and "not payable" not in n.lower()
        and "crosswalk" not in n.lower()
    ]
    if priced:
        return priced[0]
    # Fall back to any single CSV that is not clearly the not-payable/crosswalk file.
    other = [
        n for n in csvs
        if "not payable" not in n.lower() and "crosswalk" not in n.lower()
    ]
    return other[0] if other else (csvs[0] if csvs else None)


# ---------------------------------------------------------------- small helpers


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


def _cell(row: Sequence[str], header: Dict[str, int], name: str) -> str:
    """Value at a named column, stripped; empty string if missing."""
    idx = header.get(name)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def _to_float(value: Any) -> Optional[float]:
    """Parse a payment-limit / percentage cell to float; None on failure.

    Strips currency symbols, thousands separators, and stray whitespace.
    """
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_us_date(value: str) -> Optional[str]:
    """Parse a "July 1, 2026"-style date to ISO ``YYYY-MM-DD``; None on failure."""
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None
