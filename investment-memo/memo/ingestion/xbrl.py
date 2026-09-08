"""SEC EDGAR XBRL structured-facts ingester (companyfacts API).

Where :mod:`memo.ingestion.edgar` pulls filing *prose* (10-K/10-Q text), this
source pulls the *structured numeric facts* SEC exposes as XBRL, so the valuation
section can ground a number like "cash and equivalents = $124.5M as of 2026-06-30"
without scraping a prose table.

Flow:

1. Resolve a ticker to a CIK via ``company_tickers.json`` (same source of truth as
   the EDGAR ingester).
2. Load ``companyfacts/CIK{cik:010d}.json`` (one JSON per company, all reported
   XBRL facts).
3. For each curated canonical fact (cash, short-term investments, shares
   outstanding, total liabilities, total debt, R&D expense, net loss), find the
   first matching taxonomy tag the company actually reports, then take its most
   recent reported value with unit, as-of date, and the accession/form it came
   from.
4. Emit one :class:`Document` per available fact under the ``xbrl`` source, with a
   searchable sentence (so BM25 finds "cash and cash equivalents") plus the
   structured fields for exact citation.

Foreign private issuers (e.g. ABVX / Abivax, which files 20-F and reports in EUR
under IFRS) lack US-GAAP tags; each canonical fact lists IFRS fallbacks and any
concept a company simply does not report is omitted, never fabricated.

Only the standard library is used. All network access goes through ``self.http``
(the shared :class:`HttpClient`), which already sets the SEC User-Agent and
rate-limits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .base import BaseIngester, Document, HttpClient, Storage

# SEC endpoints. Ticker map is shared with the EDGAR ingester; companyfacts is the
# single per-company XBRL bundle.
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
# EDGAR filing artifacts live under the Archives host, keyed by bare CIK; used to
# build a human-openable link to the filing a fact was reported in.
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


@dataclass(frozen=True)
class ConceptSpec:
    """A curated financial fact and the taxonomy tags that can supply it.

    ``candidates`` is an ordered list of ``(taxonomy, tag)`` pairs tried in
    priority order; the first one the company actually reports wins. This is how a
    US-GAAP filer and an IFRS foreign private issuer are covered by one spec.
    """

    key: str  # stable doc_id / canonical key, e.g. "cash_and_equivalents"
    label: str  # human label, e.g. "Cash and cash equivalents"
    kind: str  # "instant" (balance-sheet point in time) or "duration" (a period)
    monetary: bool  # True for currency amounts, False for share counts
    candidates: Sequence[Tuple[str, str]]


# The focused, memo-relevant set. Order is the emit order. Each fact lists its
# US-GAAP tag first and IFRS / alternate tags after, so foreign filers still match
# where they report the same economic concept.
CONCEPTS: Sequence[ConceptSpec] = (
    ConceptSpec(
        "cash_and_equivalents",
        "Cash and cash equivalents",
        "instant",
        True,
        (
            ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
            ("ifrs-full", "CashAndCashEquivalents"),
        ),
    ),
    ConceptSpec(
        "short_term_investments",
        "Short-term investments",
        "instant",
        True,
        (
            ("us-gaap", "ShortTermInvestments"),
            ("us-gaap", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"),
            ("us-gaap", "MarketableSecuritiesCurrent"),
            ("us-gaap", "AvailableForSaleSecuritiesCurrent"),
            ("ifrs-full", "ShorttermDepositsNotClassifiedAsCashEquivalents"),
        ),
    ),
    ConceptSpec(
        "common_shares_outstanding",
        "Common shares outstanding",
        "instant",
        False,
        (("dei", "EntityCommonStockSharesOutstanding"),),
    ),
    ConceptSpec(
        "total_liabilities",
        "Total liabilities",
        "instant",
        True,
        (
            ("us-gaap", "Liabilities"),
            ("ifrs-full", "Liabilities"),
        ),
    ),
    ConceptSpec(
        "total_debt",
        "Total debt (long-term)",
        "instant",
        True,
        (
            ("us-gaap", "LongTermDebt"),
            ("us-gaap", "LongTermDebtNoncurrent"),
            ("us-gaap", "DebtLongtermAndShorttermCombinedAmount"),
        ),
    ),
    ConceptSpec(
        "research_and_development_expense",
        "Research and development expense",
        "duration",
        True,
        (
            ("us-gaap", "ResearchAndDevelopmentExpense"),
            ("us-gaap", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
            ("ifrs-full", "ResearchAndDevelopmentExpense"),
        ),
    ),
    ConceptSpec(
        "net_income_loss",
        "Net income (loss)",
        "duration",
        True,
        (
            ("us-gaap", "NetIncomeLoss"),
            ("us-gaap", "ProfitLoss"),
            ("ifrs-full", "ProfitLoss"),
        ),
    ),
)

# The derived combined fact is emitted only when BOTH components are reported at
# the exact same as-of date, so it is a real sum of two real values, never a guess.
DERIVED_CASH_STI_KEY = "cash_and_short_term_investments"
DERIVED_CASH_STI_LABEL = "Cash, cash equivalents and short-term investments"


@dataclass(frozen=True)
class SelectedFact:
    """The single most-recent reported value chosen for one concept."""

    taxonomy: str
    tag: str
    unit: str
    value: float
    end: str
    start: Optional[str]  # None for instant (balance-sheet) facts
    accession: str
    form: str
    fiscal_year: Optional[int]
    fiscal_period: Optional[str]
    filed: Optional[str]
    frame: Optional[str]


class XbrlIngester(BaseIngester):
    """Ingest structured XBRL numeric facts for a company (SEC companyfacts API).

    Usage::

        manifest = XbrlIngester().run("KYMR")

    ``fetch`` / ``run`` accept no source-specific options; the curated
    :data:`CONCEPTS` set is fixed. Each emitted :class:`Document` is one financial
    fact at its most recent as-of date, citable down to the accession and tag.
    """

    source = "xbrl"

    def __init__(
        self,
        http: Optional[HttpClient] = None,
        storage: Optional[Storage] = None,
    ) -> None:
        super().__init__(http=http, storage=storage)
        # ticker (upper) -> {"cik": int, "title": str}; cached across calls.
        self._ticker_map: Optional[Dict[str, Dict[str, Any]]] = None

    # ------------------------------------------------------------------ public

    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Fetch structured XBRL facts for ``company`` (a ticker, e.g. "KYMR")."""
        ticker = company.strip().upper()
        cik, company_title = self._resolve_cik(ticker)
        companyfacts = self._load_companyfacts(cik)
        entity_name = str(companyfacts.get("entityName") or company_title)
        facts = companyfacts.get("facts", {}) or {}

        selected: Dict[str, SelectedFact] = {}
        for spec in CONCEPTS:
            chosen = self._select_concept(facts, spec)
            if chosen is not None:
                selected[spec.key] = chosen

        reporting_currency = self._reporting_currency(selected)

        documents: List[Document] = []
        for spec in CONCEPTS:
            fact = selected.get(spec.key)
            if fact is None:
                continue  # concept not reported by this company; omit, never invent
            documents.append(
                self._build_document(
                    ticker, cik, entity_name, reporting_currency, spec, fact
                )
            )

        derived = self._derive_cash_plus_sti(selected)
        if derived is not None:
            documents.append(
                self._build_derived_document(
                    ticker, cik, entity_name, reporting_currency, derived
                )
            )
        return documents

    # ----------------------------------------------------------------- helpers

    def _resolve_cik(self, ticker: str) -> Tuple[int, str]:
        """Resolve a ticker to (CIK int, company title). Cached per instance."""
        if self._ticker_map is None:
            self._ticker_map = self._load_ticker_map()
        entry = self._ticker_map.get(ticker)
        if entry is None:
            raise ValueError(
                f"Unknown ticker {ticker!r}: not found in SEC company_tickers.json"
            )
        return int(entry["cik"]), str(entry.get("title", ticker))

    def _load_ticker_map(self) -> Dict[str, Dict[str, Any]]:
        """Fetch and index company_tickers.json as ticker -> {cik, title}."""
        payload = self.http.get_json(TICKERS_URL)
        mapping: Dict[str, Dict[str, Any]] = {}
        for entry in payload.values():
            ticker = str(entry.get("ticker", "")).upper()
            if not ticker:
                continue
            mapping[ticker] = {
                "cik": int(entry["cik_str"]),
                "title": entry.get("title", ticker),
            }
        return mapping

    def _load_companyfacts(self, cik: int) -> Dict[str, Any]:
        """Fetch the companyfacts bundle for a CIK."""
        return self.http.get_json(COMPANYFACTS_URL.format(cik=cik))

    @staticmethod
    def _select_concept(
        facts: Dict[str, Any], spec: ConceptSpec
    ) -> Optional[SelectedFact]:
        """Pick the most-recent reported value for a concept from its candidate tags.

        Each candidate ``(taxonomy, tag)`` is reduced to its own most-recent fact,
        and the candidate whose fact has the latest period end wins; candidate
        priority (list order) only breaks ties on the end date. Choosing by recency
        rather than by "first tag that exists" matters because a company can abandon
        one tag for another mid-history (e.g. IMVT's R&D or COGT's short-term
        investments), leaving a stale tag that must not shadow the current one.

        Within a tag, the best unit is the one carrying the latest observation;
        within a unit, the most recent fact is the one with the latest period end,
        breaking ties by latest filing date and then by longest period (so a
        year-to-date / annual figure wins over a same-end stub). ``start``/``end``/
        ``form`` are all recorded, so the citation states its own period.
        """
        overall: Optional[SelectedFact] = None
        for taxonomy, tag in spec.candidates:
            concept = facts.get(taxonomy, {}).get(tag)
            if not concept:
                continue
            best = XbrlIngester._most_recent_in_concept(taxonomy, tag, concept)
            # Strict ">" keeps the earlier (higher-priority) candidate on a tie.
            if best is not None and (overall is None or best.end > overall.end):
                overall = best
        return overall

    @staticmethod
    def _most_recent_in_concept(
        taxonomy: str, tag: str, concept: Dict[str, Any]
    ) -> Optional[SelectedFact]:
        """Most-recent fact within a single concept (across its units)."""
        units: Dict[str, Any] = concept.get("units", {}) or {}
        best: Optional[SelectedFact] = None
        best_sort: Optional[Tuple[str, str, int]] = None
        for unit, entries in units.items():
            for entry in entries:
                if entry.get("val") is None or not entry.get("end"):
                    continue
                end = str(entry["end"])
                start = str(entry["start"]) if entry.get("start") else None
                filed = str(entry.get("filed") or "")
                sort_key = (end, filed, _period_days(start, end))
                if best_sort is None or sort_key > best_sort:
                    best_sort = sort_key
                    best = SelectedFact(
                        taxonomy=taxonomy,
                        tag=tag,
                        unit=unit,
                        value=float(entry["val"]),
                        end=end,
                        start=start,
                        accession=str(entry.get("accn") or ""),
                        form=str(entry.get("form") or ""),
                        fiscal_year=entry.get("fy"),
                        fiscal_period=entry.get("fp"),
                        filed=filed or None,
                        frame=entry.get("frame"),
                    )
        return best

    @staticmethod
    def _reporting_currency(selected: Dict[str, SelectedFact]) -> str:
        """Infer the reporting currency from the monetary facts (e.g. USD, EUR)."""
        for key in ("net_income_loss", "cash_and_equivalents", "total_liabilities"):
            fact = selected.get(key)
            if fact and fact.unit and fact.unit != "shares":
                return fact.unit
        for fact in selected.values():
            if fact.unit and fact.unit != "shares" and fact.unit != "pure":
                return fact.unit
        return ""

    @staticmethod
    def _derive_cash_plus_sti(
        selected: Dict[str, SelectedFact],
    ) -> Optional[Tuple[SelectedFact, SelectedFact, float]]:
        """Combine cash + short-term investments only when they share an as-of date.

        Returns ``(cash_fact, sti_fact, total)`` or None. Summing two values
        reported for the same balance-sheet date is a real total; mismatched dates
        would not be, so the derived fact is simply not emitted then.
        """
        cash = selected.get("cash_and_equivalents")
        sti = selected.get("short_term_investments")
        if cash is None or sti is None:
            return None
        if cash.unit != sti.unit or cash.end != sti.end:
            return None
        if sti.value == 0:
            return None  # nothing to add; the sum would just restate cash
        return cash, sti, cash.value + sti.value

    # ------------------------------------------------------------ doc assembly

    def _filing_index_url(self, cik: int, accession: str) -> str:
        """Human-openable filing index page for the accession a fact came from."""
        if not accession:
            return COMPANYFACTS_URL.format(cik=cik)
        nodash = accession.replace("-", "")
        return f"{ARCHIVES_BASE}/{cik}/{nodash}/{accession}-index.htm"

    def _build_document(
        self,
        ticker: str,
        cik: int,
        entity_name: str,
        reporting_currency: str,
        spec: ConceptSpec,
        fact: SelectedFact,
    ) -> Document:
        metadata: Dict[str, Any] = {
            "cik": cik,
            "entity_name": entity_name,
            "concept": spec.key,
            "label": spec.label,
            "taxonomy": fact.taxonomy,
            "tag": fact.tag,
            "value": fact.value,
            "unit": fact.unit,
            "reporting_currency": reporting_currency if spec.monetary else "",
            "period_end": fact.end,
            "period_start": fact.start,
            "period_type": spec.kind,
            "accession": fact.accession,
            "form": fact.form,
            "fiscal_year": fact.fiscal_year,
            "fiscal_period": fact.fiscal_period,
            "filed": fact.filed,
            "frame": fact.frame,
            "companyfacts_url": COMPANYFACTS_URL.format(cik=cik),
            "derived": False,
        }
        text = self._fact_sentence(entity_name, spec, fact)
        return Document(
            company=ticker,
            source=self.source,
            doc_type="xbrl_fact",
            doc_id=spec.key,
            title=self._title(entity_name, spec, fact),
            url=self._filing_index_url(cik, fact.accession),
            published=fact.end,
            metadata=metadata,
            text=text,
            raw=json.dumps(
                {
                    "concept": spec.key,
                    "taxonomy": fact.taxonomy,
                    "tag": fact.tag,
                    "unit": fact.unit,
                    "value": fact.value,
                    "start": fact.start,
                    "end": fact.end,
                    "accn": fact.accession,
                    "form": fact.form,
                    "fy": fact.fiscal_year,
                    "fp": fact.fiscal_period,
                    "filed": fact.filed,
                    "frame": fact.frame,
                },
                sort_keys=True,
            ),
        )

    def _build_derived_document(
        self,
        ticker: str,
        cik: int,
        entity_name: str,
        reporting_currency: str,
        derived: Tuple[SelectedFact, SelectedFact, float],
    ) -> Document:
        cash, sti, total = derived
        metadata: Dict[str, Any] = {
            "cik": cik,
            "entity_name": entity_name,
            "concept": DERIVED_CASH_STI_KEY,
            "label": DERIVED_CASH_STI_LABEL,
            "value": total,
            "unit": cash.unit,
            "reporting_currency": reporting_currency,
            "period_end": cash.end,
            "period_type": "instant",
            "derived": True,
            "components": [
                {
                    "concept": "cash_and_equivalents",
                    "taxonomy": cash.taxonomy,
                    "tag": cash.tag,
                    "value": cash.value,
                    "accession": cash.accession,
                },
                {
                    "concept": "short_term_investments",
                    "taxonomy": sti.taxonomy,
                    "tag": sti.tag,
                    "value": sti.value,
                    "accession": sti.accession,
                },
            ],
            "companyfacts_url": COMPANYFACTS_URL.format(cik=cik),
        }
        text = (
            f"{entity_name}: {DERIVED_CASH_STI_LABEL.lower()} totaled "
            f"{_money(total, cash.unit)} ({_amount(total)} {cash.unit}) as of "
            f"{cash.end}. Derived as cash and cash equivalents "
            f"({_money(cash.value, cash.unit)}) plus short-term investments "
            f"({_money(sti.value, sti.unit)}), both reported at {cash.end}."
        )
        return Document(
            company=ticker,
            source=self.source,
            doc_type="xbrl_fact",
            doc_id=DERIVED_CASH_STI_KEY,
            title=f"{entity_name} - {DERIVED_CASH_STI_LABEL} ({cash.end})",
            url=self._filing_index_url(cik, cash.accession),
            published=cash.end,
            metadata=metadata,
            text=text,
            raw=json.dumps(
                {
                    "concept": DERIVED_CASH_STI_KEY,
                    "unit": cash.unit,
                    "value": total,
                    "end": cash.end,
                    "cash": cash.value,
                    "short_term_investments": sti.value,
                },
                sort_keys=True,
            ),
        )

    @staticmethod
    def _title(entity_name: str, spec: ConceptSpec, fact: SelectedFact) -> str:
        return f"{entity_name} - {spec.label} ({fact.end})"

    @staticmethod
    def _fact_sentence(entity_name: str, spec: ConceptSpec, fact: SelectedFact) -> str:
        """A searchable, human sentence naming concept, value, unit and date.

        Both the exact value and a rounded $-abbreviation are included so BM25 can
        match either a raw figure or a phrase like "cash and cash equivalents", and
        so a reader sees a citable, self-describing statement.
        """
        if spec.monetary:
            amount = f"{_money(fact.value, fact.unit)} ({_amount(fact.value)} {fact.unit})"
        else:
            amount = f"{_amount(fact.value)} {fact.unit}"

        if spec.kind == "duration" and fact.start:
            when = f"for the period {fact.start} to {fact.end}"
        else:
            when = f"as of {fact.end}"

        parts = [f"{entity_name}: {spec.label} was {amount} {when}."]
        source_bits = []
        if fact.form:
            source_bits.append(f"form {fact.form}")
        if fact.accession:
            source_bits.append(f"accession {fact.accession}")
        if fact.filed:
            source_bits.append(f"filed {fact.filed}")
        if source_bits:
            parts.append("Reported in " + ", ".join(source_bits) + ".")
        parts.append(f"Source XBRL tag: {fact.taxonomy}:{fact.tag}.")
        return " ".join(parts)


# ---------------------------------------------------------------- helpers


def _period_days(start: Optional[str], end: str) -> int:
    """Length of a fact's period in days; 0 for an instant (no start).

    Used only as a deterministic tie-breaker when two facts share the same end and
    filing dates (e.g. a quarterly and a year-to-date value in the same filing).
    """
    if not start:
        return 0
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError:
        return 0
    return (e - s).days


def _amount(value: float) -> str:
    """Full numeric amount with thousands separators (no currency symbol)."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _money(value: float, unit: str) -> str:
    """Rounded, abbreviated amount for readability, e.g. $124.5M or EUR 30.2M.

    Non-currency units (e.g. share counts) fall back to a plain grouped number.
    """
    symbol = "$" if unit == "USD" else (f"{unit} " if unit and unit != "pure" else "")
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        body = f"{value / 1_000_000_000:.2f}B"
    elif magnitude >= 1_000_000:
        body = f"{value / 1_000_000:.1f}M"
    elif magnitude >= 1_000:
        body = f"{value / 1_000:.1f}K"
    else:
        body = f"{value:,.0f}"
    return f"{symbol}{body}"
