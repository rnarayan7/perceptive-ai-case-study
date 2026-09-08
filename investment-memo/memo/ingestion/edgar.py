"""SEC EDGAR ingester.

Pulls recent filings for a public company from SEC EDGAR and normalizes them
into :class:`~memo.ingestion.base.Document` records. It focuses on the forms that
matter for a biopharma investment memo: annual/quarterly/current reports,
registration and prospectus filings, and the foreign-private-issuer equivalents
(20-F / 6-K) so companies like ABVX are covered alongside domestic filers.

Flow:

1. Resolve a ticker to a CIK via ``company_tickers.json`` (cached per instance).
2. Load the company's submissions index (``CIK{cik:010d}.json``).
3. Walk the ``filings.recent`` column arrays, keeping matching form types.
4. Emit one ``Document`` per filing; for the most recent few, fetch the primary
   document and render it to plain text.

Only the standard library is used. All network access goes through
``self.http`` (the shared :class:`HttpClient`), which already sets a descriptive
User-Agent and rate-limits to stay within SEC etiquette.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set

from .base import BaseIngester, Document, HttpClient, Storage

# SEC endpoints.
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# EDGAR filing artifacts live under the Archives host, keyed by bare CIK.
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# Forms worth pulling for a biopharma memo. 424B* is matched by prefix so every
# prospectus variant (424B1..424B5, 424B7, ...) is included. 20-F and 6-K cover
# foreign private issuers.
DEFAULT_FORMS: Set[str] = {
    "10-K",
    "10-Q",
    "8-K",
    "S-1",
    "20-F",
    "6-K",
}
# Prefixes matched loosely (form.startswith(prefix)) to catch amendments and
# numbered prospectus variants.
DEFAULT_FORM_PREFIXES: Sequence[str] = ("424B",)

DEFAULT_LIMIT = 25
DEFAULT_PRIMARY_TEXT_COUNT = 5


@dataclass(frozen=True)
class FilingRow:
    """One filing pulled from the ``filings.recent`` parallel arrays."""

    accession: str  # e.g. "0001628280-24-001234"
    form: str
    filing_date: str
    report_date: str
    primary_document: str
    primary_doc_description: str
    items: str

    @property
    def accession_nodash(self) -> str:
        """Accession number with dashes stripped (used in Archives paths)."""
        return self.accession.replace("-", "")


class _HTMLTextExtractor(HTMLParser):
    """Collapses an HTML document to readable plain text.

    A deliberately small stdlib parser: it drops script/style content, inserts
    line breaks around block-level tags, and accumulates the remaining text. It
    is not a full renderer (no table layout, no CSS), but it is enough to make a
    filing's prose searchable and readable.
    """

    _BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "table", "h1", "h2", "h3", "h4",
        "h5", "h6", "section", "article", "hr", "ul", "ol", "td", "th",
    }
    _SKIP_TAGS = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        # Normalize whitespace: collapse runs of blank lines, trim each line.
        lines = [line.strip() for line in raw.splitlines()]
        out: List[str] = []
        blank = False
        for line in lines:
            if line:
                out.append(line)
                blank = False
            elif not blank:
                out.append("")
                blank = True
        return "\n".join(out).strip()


# --------------------------------------------------------------- segmentation

# A filing's body is organized under standard SEC "Item N." headers, optionally
# grouped by "PART I / PART II" (10-Qs repeat item numbers across parts). The
# regexes below detect those headers at the start of a line. Detection is
# case-insensitive and tolerant of the whitespace and stray punctuation that the
# HTML-to-text pass leaves behind.
_PART_RE = re.compile(r"^\s*part\s+([ivx]+)\b", re.IGNORECASE)
# Item number: 1-2 digits, an optional ".NN" sub-item (8-K "Item 8.01"), and an
# optional trailing letter ("Item 1A"). A header ends with punctuation, end of
# line (bare TOC-style header), or whitespace before a capitalized title word.
_ITEM_RE = re.compile(
    r"^\s*item\s+(\d{1,2}(?:\.\d{1,2})?)\s*([a-z]?)\s*"
    r"(?:[.:)–—\-]|(?=\s+[A-Z])|$)",
    re.IGNORECASE,
)
# A line that is just a page number (common between TOC entries).
_PAGE_RE = re.compile(r"^\s*\d{1,4}\s*$")


@dataclass(frozen=True)
class FilingSection:
    """One segmented section of a filing's primary document."""

    key: str  # filesystem-safe suffix, e.g. "pii-item1a" or "cover"
    label: str  # human label, e.g. "Risk Factors"
    text: str
    boilerplate: bool = False  # repeats quarter-over-quarter (e.g. risk factors)


def _clean_label(value: str) -> str:
    """Trim a raw header remainder into a tidy section label."""
    value = value.strip().strip(".:)-–— ").strip()
    return re.sub(r"\s+", " ", value)


def _section_label(line: str, header_end: int, lines: Sequence[str], i: int) -> str:
    """Label for a header: its inline title, else the next real line."""
    inline = _clean_label(line[header_end:])
    if inline:
        return inline
    # Bare header ("Item 1A." on its own line); look ahead for the title,
    # skipping blank lines and lone page numbers.
    for j in range(i + 1, min(i + 4, len(lines))):
        nxt = lines[j].strip()
        if not nxt or _PAGE_RE.match(nxt):
            continue
        if _ITEM_RE.match(nxt) or _PART_RE.match(nxt):
            break
        return _clean_label(nxt)
    return ""


def _is_boilerplate(item: str, label: str) -> bool:
    """Flag the risk-factors section, which repeats near-verbatim each period."""
    return item.upper() == "1A" or "risk factor" in label.lower()


def segment_sections(text: str) -> List[FilingSection]:
    """Split a filing's plain text into sections by SEC "Item N." headers.

    Returns one :class:`FilingSection` per detected section, in document order,
    with any leading cover page / table of contents preserved as a ``cover``
    section so no content is lost. A filing's table of contents lists the same
    item headers as the body; because the body always follows the TOC, the *last*
    occurrence of each ``(part, item)`` header is taken as the real boundary,
    which discards the TOC copies without a fragile TOC heuristic.

    Returns an empty list when fewer than two reliable boundaries are found, which
    the caller treats as the signal to keep the filing as a single Document.
    """
    lines = text.split("\n")
    part = ""
    headers: List["tuple[int, str, str, str]"] = []  # (line_idx, key, item, label)
    for i, line in enumerate(lines):
        part_match = _PART_RE.match(line)
        if part_match:
            part = part_match.group(1).upper()
            continue
        item_match = _ITEM_RE.match(line)
        if not item_match:
            continue
        number = item_match.group(1)
        letter = (item_match.group(2) or "").upper()
        item = f"{number}{letter}"
        item_key = item.lower().replace(".", "_")
        key = (f"p{part.lower()}-" if part else "") + f"item{item_key}"
        label = _section_label(line, item_match.end(), lines, i)
        headers.append((i, key, item, label))

    # Keep the last occurrence of each key (body wins over table of contents).
    last_by_key: Dict[str, "tuple[int, str, str, str]"] = {}
    for header in headers:
        last_by_key[header[1]] = header
    boundaries = sorted(last_by_key.values(), key=lambda h: h[0])
    if len(boundaries) < 2:
        return []  # no reliable segmentation; caller falls back to one Document

    sections: List[FilingSection] = []
    first_line = boundaries[0][0]
    preamble = "\n".join(lines[:first_line]).strip()
    if preamble:
        sections.append(
            FilingSection("cover", "Cover & Table of Contents", preamble)
        )
    for pos, (line_idx, key, item, label) in enumerate(boundaries):
        end = boundaries[pos + 1][0] if pos + 1 < len(boundaries) else len(lines)
        body = "\n".join(lines[line_idx:end]).strip()
        if not body:
            continue
        final_label = label or f"Item {item}"
        sections.append(
            FilingSection(key, final_label, body, _is_boilerplate(item, final_label))
        )
    return sections


class EdgarIngester(BaseIngester):
    """Ingest recent SEC EDGAR filings for a public company.

    Usage::

        manifest = EdgarIngester().run("KYMR", limit=10, primary_text_count=3)

    Options accepted by :meth:`fetch` / :meth:`run` (via ``**options``):

    ``forms``
        Iterable of exact form types to keep (default :data:`DEFAULT_FORMS`).
    ``form_prefixes``
        Iterable of form prefixes matched loosely (default ``("424B",)``).
    ``limit``
        Max number of most-recent matching filings to emit (default 25).
    ``primary_text_count``
        How many of the most-recent filings get their primary document fetched
        and rendered to text (default 5). The rest are metadata-only.
    """

    source = "edgar"

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
        """Fetch recent EDGAR filings for ``company`` (a ticker, e.g. "KYMR")."""
        forms = {f.upper() for f in options.get("forms", DEFAULT_FORMS)}
        prefixes = tuple(options.get("form_prefixes", DEFAULT_FORM_PREFIXES))
        limit = int(options.get("limit", DEFAULT_LIMIT))
        primary_text_count = int(options.get("primary_text_count", DEFAULT_PRIMARY_TEXT_COUNT))

        ticker = company.strip().upper()
        cik, company_title = self._resolve_cik(ticker)
        submissions = self._load_submissions(cik)

        matching = [
            row
            for row in self._iter_recent_filings(submissions)
            if self._form_matches(row.form, forms, prefixes)
        ]
        # filings.recent is already newest-first; keep the most recent `limit`.
        matching = matching[:limit]

        documents: List[Document] = []
        for index, row in enumerate(matching):
            want_text = index < primary_text_count
            documents.extend(
                self._build_documents(ticker, cik, company_title, row, want_text)
            )
        return documents

    # ----------------------------------------------------------------- helpers

    def _resolve_cik(self, ticker: str) -> "tuple[int, str]":
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
        # Shape: {"0": {"cik_str": 123, "ticker": "AAA", "title": "..."}, ...}
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

    def _load_submissions(self, cik: int) -> Dict[str, Any]:
        """Fetch the submissions index JSON for a CIK."""
        return self.http.get_json(SUBMISSIONS_URL.format(cik=cik))

    def _iter_recent_filings(self, submissions: Dict[str, Any]) -> Iterator[FilingRow]:
        """Yield ``FilingRow`` objects from the ``filings.recent`` column arrays.

        EDGAR returns each field as its own parallel array, so row *i* is
        assembled by indexing every column at *i*.
        """
        recent = submissions.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])
        primary_descs = recent.get("primaryDocDescription", [])
        items = recent.get("items", [])

        def at(seq: Sequence[Any], i: int) -> str:
            return str(seq[i]) if i < len(seq) and seq[i] is not None else ""

        for i in range(len(accessions)):
            yield FilingRow(
                accession=at(accessions, i),
                form=at(forms, i),
                filing_date=at(filing_dates, i),
                report_date=at(report_dates, i),
                primary_document=at(primary_docs, i),
                primary_doc_description=at(primary_descs, i),
                items=at(items, i),
            )

    @staticmethod
    def _form_matches(
        form: str, forms: Set[str], prefixes: Sequence[str]
    ) -> bool:
        """True if ``form`` is a wanted exact form or matches a wanted prefix.

        Amendments (e.g. "10-K/A") are matched off their base form.
        """
        upper = form.upper()
        base = upper.split("/", 1)[0]
        if upper in forms or base in forms:
            return True
        return any(upper.startswith(p) for p in prefixes)

    def _filing_index_url(self, cik: int, row: FilingRow) -> str:
        """URL of the human-readable filing index page on sec.gov."""
        return (
            f"{ARCHIVES_BASE}/{cik}/{row.accession_nodash}/"
            f"{row.accession}-index.htm"
        )

    def _primary_document_url(self, cik: int, row: FilingRow) -> str:
        """URL of the filing's primary document."""
        return f"{ARCHIVES_BASE}/{cik}/{row.accession_nodash}/{row.primary_document}"

    def _build_documents(
        self,
        ticker: str,
        cik: int,
        company_title: str,
        row: FilingRow,
        want_text: bool,
    ) -> List[Document]:
        """Assemble one or more normalized ``Document`` objects for a filing row.

        A filing whose primary document is fetched and segments cleanly into SEC
        "Item N." sections becomes one Document *per section*, each with a unique
        ``doc_id`` so a citation can point at a specific section (and repetitive
        risk-factor boilerplate can be down-weighted) rather than the whole
        filing. Metadata-only filings, unfetched text, and filings without
        reliable section boundaries stay a single Document as before.
        """
        index_url = self._filing_index_url(cik, row)
        metadata: Dict[str, Any] = {
            "accession": row.accession,
            "cik": cik,
            "form": row.form,
            "primary_document": row.primary_document,
            "primary_doc_description": row.primary_doc_description,
            "report_date": row.report_date,
            "filing_date": row.filing_date,
            "items": row.items,
            "company_title": company_title,
            "primary_document_url": self._primary_document_url(cik, row)
            if row.primary_document
            else "",
        }

        text = ""
        raw = ""
        if want_text and row.primary_document:
            try:
                fetched, text = self._fetch_primary_document(cik, row)
                raw = fetched
            except Exception as exc:  # noqa: BLE001 - keep the doc, note the gap
                metadata["textError"] = f"{type(exc).__name__}: {exc}"

        sections = segment_sections(text) if text else []
        if sections:
            base_id = row.accession_nodash or row.accession
            base_title = self._make_title(company_title, row)
            documents: List[Document] = []
            for section in sections:
                section_meta = dict(metadata)
                section_meta["section"] = section.label
                section_meta["section_key"] = section.key
                if section.boilerplate:
                    section_meta["boilerplate"] = True
                documents.append(
                    Document(
                        company=ticker,
                        source=self.source,
                        doc_type=row.form,
                        doc_id=f"{base_id}-{section.key}",
                        title=f"{base_title} - {section.label}",
                        url=index_url,
                        published=row.filing_date or None,
                        metadata=section_meta,
                        # Each section carries its own text as the raw payload so
                        # change detection is per-section and the full HTML is not
                        # duplicated across every section record.
                        text=section.text,
                        raw=section.text,
                    )
                )
            return documents

        if not raw:
            # Metadata-only filing: preserve the recent-entry fields as raw JSON.
            raw = json.dumps(metadata, indent=2, sort_keys=True)

        return [
            Document(
                company=ticker,
                source=self.source,
                doc_type=row.form,
                doc_id=row.accession_nodash or row.accession,
                title=self._make_title(company_title, row),
                url=index_url,
                published=row.filing_date or None,
                metadata=metadata,
                text=text,
                raw=raw,
            )
        ]

    def _fetch_primary_document(self, cik: int, row: FilingRow) -> "tuple[str, str]":
        """Fetch a filing's primary document; return (raw, plain_text).

        Non-HTML primary documents (e.g. plain-text .txt filings) are returned
        as-is for the text field.
        """
        url = self._primary_document_url(cik, row)
        raw = self.http.get_text(url)
        doc = row.primary_document.lower()
        if doc.endswith((".htm", ".html")) or "<html" in raw[:2048].lower():
            text = self._html_to_text(raw)
        else:
            text = raw.strip()
        return raw, text

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Render HTML to readable plain text using the stdlib parser."""
        parser = _HTMLTextExtractor()
        parser.feed(html)
        parser.close()
        return parser.get_text()

    @staticmethod
    def _make_title(company_title: str, row: FilingRow) -> str:
        """Build a human-readable filing title."""
        desc = row.primary_doc_description.strip()
        if desc and desc.upper() != row.form.upper():
            return f"{row.form} - {desc} ({row.filing_date})"
        return f"{company_title} {row.form} ({row.filing_date})"
