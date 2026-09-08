"""SEC EDGAR investor decks as a figure source.

Development-stage biotechs file their corporate/investor presentations as 8-K
exhibits (typically Exhibit 99.x). These decks are the *production distribution*
the extractor really faces: compressed, brand-colored, footnote-cluttered slides.
This ingester resolves a ticker to a CIK, finds recent 8-K filings, pulls the
presentation exhibits (PDF or image), and extracts their figures.

Ground truth is weaker here than for papers or FDA docs: a deck shows the figure
but often not every number, so recovered values are candidates to be confirmed
against the matching press release or abstract. That is the honest tradeoff for
gaining realism.

Only the standard library plus poppler (for PDF exhibits) is used.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from corpus.base import (
    BaseFigureIngester,
    FigureRecord,
    FigureType,
    extract_candidate_values,
)
from corpus.pdf_figures import extract_embedded_images, poppler_available, render_pages

logger = logging.getLogger("corpus.edgar_deck")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

DEFAULT_FILING_LIMIT = 8  # 8-K filings to scan, newest first
DEFAULT_MAX_FIGURES = 40
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")


class EdgarDeckIngester(BaseFigureIngester):
    """Extract figures from a company's 8-K investor-presentation exhibits.

    ``fetch`` takes a ticker as ``query`` (e.g. ``"COGT"``).

    Options:

    ``filing_limit``
        Number of recent 8-K filings to scan (default 8).
    ``max_figures``
        Cap on total figures returned (default 40).
    ``exhibit_keywords``
        Lowercase substrings that mark a document as a presentation
        (default: presentation/investor/corporate/slide/ex99/ex-99).
    """

    source = "edgar_deck"

    def __init__(self, http=None, storage=None) -> None:
        super().__init__(http=http, storage=storage)
        self._ticker_map: Optional[Dict[str, Dict[str, Any]]] = None

    def fetch(self, query: str, **options: Any) -> Iterable[FigureRecord]:
        filing_limit = int(options.get("filing_limit") or DEFAULT_FILING_LIMIT)
        max_figures = int(options.get("max_figures") or DEFAULT_MAX_FIGURES)
        keywords = tuple(options.get("exhibit_keywords") or (
            "presentation", "investor", "corporate", "slide", "ex99", "ex-99",
        ))

        ticker = query.strip().upper()
        cik, title = self._resolve_cik(ticker)
        submissions = self.http.get_json(SUBMISSIONS_URL.format(cik=cik))
        accessions = self._recent_8k_accessions(submissions, filing_limit)

        records: List[FigureRecord] = []
        for accession in accessions:
            if len(records) >= max_figures:
                break
            try:
                exhibits = self._presentation_exhibits(cik, accession, keywords)
                for doc_name in exhibits:
                    if len(records) >= max_figures:
                        break
                    records.extend(self._figures_from_exhibit(
                        ticker, cik, title, accession, doc_name,
                        remaining=max_figures - len(records),
                    ))
            except Exception as exc:  # noqa: BLE001 - skip a bad filing, keep going
                logger.warning("edgar_deck: failed on %s/%s: %s", cik, accession, exc)
        return records

    # -- ticker / filing discovery -----------------------------------------

    def _resolve_cik(self, ticker: str) -> "tuple[int, str]":
        if self._ticker_map is None:
            payload = self.http.get_json(TICKERS_URL)
            self._ticker_map = {
                str(e.get("ticker", "")).upper(): {
                    "cik": int(e["cik_str"]), "title": e.get("title", "")
                }
                for e in payload.values() if e.get("ticker")
            }
        entry = self._ticker_map.get(ticker)
        if entry is None:
            raise ValueError(f"Unknown ticker {ticker!r} in SEC company_tickers.json")
        return entry["cik"], entry["title"]

    def _recent_8k_accessions(self, submissions: Dict[str, Any], limit: int) -> List[str]:
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        out: List[str] = []
        for form, accession in zip(forms, accessions):
            if form.upper().split("/", 1)[0] == "8-K":
                out.append(accession.replace("-", ""))
                if len(out) >= limit:
                    break
        return out

    def _presentation_exhibits(
        self, cik: int, accession: str, keywords: tuple
    ) -> List[str]:
        """List document names in a filing that look like presentation exhibits."""
        index_url = f"{ARCHIVES_BASE}/{cik}/{accession}/index.json"
        index = self.http.get_json(index_url)
        items = ((index.get("directory") or {}).get("item")) or []
        names: List[str] = []
        for item in items:
            name = str(item.get("name", ""))
            lower = name.lower()
            if not lower.endswith((".pdf",) + _IMAGE_SUFFIXES):
                continue
            if any(kw in lower for kw in keywords) or lower.endswith(".pdf"):
                names.append(name)
        return names

    # -- figure extraction --------------------------------------------------

    def _figures_from_exhibit(
        self, ticker: str, cik: int, title: str, accession: str, doc_name: str, remaining: int
    ) -> List[FigureRecord]:
        url = f"{ARCHIVES_BASE}/{cik}/{accession}/{doc_name}"
        lower = doc_name.lower()
        filing_url = f"{ARCHIVES_BASE}/{cik}/{accession}/"

        if lower.endswith(_IMAGE_SUFFIXES):
            # The exhibit is itself a single image (some decks are filed as pages).
            data = self.http.get_bytes(url)
            ext = lower.rsplit(".", 1)[-1]
            return [FigureRecord(
                source=self.source,
                figure_id=f"{ticker}_{accession}_{doc_name}",
                figure_type=FigureType.UNKNOWN,
                title=f"{title} 8-K exhibit {doc_name}",
                url=filing_url, image_url=url, image_ext=ext, image_bytes=data,
                metadata={"ticker": ticker, "accession": accession, "exhibit": doc_name},
            )]

        if not poppler_available():
            logger.info("edgar_deck: skipping PDF exhibit (no poppler): %s", doc_name)
            return []

        pdf_bytes = self.http.get_bytes(url)
        images = extract_embedded_images(pdf_bytes)
        if not images:
            images = render_pages(pdf_bytes, dpi=150)
        images = images[:remaining]

        records: List[FigureRecord] = []
        for image in images:
            page_text = image.page_text or ""
            records.append(FigureRecord(
                source=self.source,
                figure_id=f"{ticker}_{accession}_{doc_name}_fig{image.index:03d}",
                figure_type=FigureType.classify(title, page_text[:2000]),
                title=f"{title} 8-K deck figure {image.index}",
                context=page_text[:8000],
                url=filing_url, image_url=url, image_ext=image.ext, image_bytes=image.data,
                published=None,
                ground_truth=extract_candidate_values(page_text),
                metadata={
                    "ticker": ticker, "accession": accession, "exhibit": doc_name,
                    "image_width": image.width, "image_height": image.height,
                },
            ))
        return records
