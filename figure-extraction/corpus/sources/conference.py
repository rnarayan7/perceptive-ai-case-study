"""Conference posters/abstracts (ASCO, ESMO, AACR, ASH).

Honest boundary: conference *abstracts* are free and print the numbers, but the
figure-bearing *posters* are gated to registered attendees at meeting time. The
public route that stays within our constraints is that companies re-post their
own posters on their IR "Posters & Presentations" pages and as SEC exhibits.

So this ingester does not crawl a conference site. Given a seed list of poster
URLs (PDF or image) it has already located, it ingests them: extracting figures,
classifying type, and recovering candidate ground truth from the poster text.
Without a seed it raises with guidance rather than pretending to have data.

For the *numeric* ground truth behind a poster figure, pair it downstream with
the free abstract text (which the ``pmc`` source often already carries) or with
``ctgov`` by trial id.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List

from corpus.base import (
    BaseFigureIngester,
    FigureRecord,
    FigureType,
    extract_candidate_values,
)
from corpus.pdf_figures import extract_embedded_images, poppler_available, render_pages

logger = logging.getLogger("corpus.conference")

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")


class ConferenceIngester(BaseFigureIngester):
    """Ingest conference posters from a supplied seed of public poster URLs.

    ``fetch`` requires the poster URLs; it does not discover them. Pass them as a
    comma-separated ``query`` or as ``poster_urls=[...]``.

    Options:

    ``poster_urls``
        Iterable of poster URLs (PDF or image).
    ``meeting``
        Label for the meeting (e.g. ``"ASCO 2026"``), stored in metadata.
    ``max_figures``
        Cap on figures per poster (default 20).
    """

    source = "conference"

    def fetch(self, query: str, **options: Any) -> Iterable[FigureRecord]:
        urls = list(options.get("poster_urls") or [])
        urls += [u.strip() for u in (query or "").split(",") if u.strip().startswith("http")]
        if not urls:
            raise RuntimeError(
                "conference ingestion needs a seed of public poster URLs it does "
                "not crawl. Pass query=<url[,url...]> or poster_urls=[...] "
                "(companies re-post posters on their IR pages and as SEC exhibits). "
                "For the numbers, pair posters with the free abstract or with ctgov."
            )
        meeting = options.get("meeting", "conference")
        max_figures = int(options.get("max_figures") or 20)

        records: List[FigureRecord] = []
        for doc_index, url in enumerate(urls):
            try:
                records.extend(self._figures_from_url(url, meeting, doc_index, max_figures))
            except Exception as exc:  # noqa: BLE001 - skip a bad poster, keep going
                logger.warning("conference: failed on %s: %s", url, exc)
        return records

    def _figures_from_url(
        self, url: str, meeting: str, doc_index: int, max_figures: int
    ) -> List[FigureRecord]:
        lower = url.lower()
        if lower.endswith(_IMAGE_SUFFIXES):
            data = self.http.get_bytes(url)
            ext = lower.rsplit(".", 1)[-1].split("?", 1)[0]
            return [FigureRecord(
                source=self.source,
                figure_id=f"{_slug(meeting)}_{doc_index}_poster",
                figure_type=FigureType.UNKNOWN,
                title=f"{meeting} poster {doc_index}",
                url=url, image_url=url, image_ext=ext, image_bytes=data,
                metadata={"meeting": meeting},
            )]

        if not poppler_available():
            raise RuntimeError("PDF poster needs poppler on PATH.")
        pdf_bytes = self.http.get_bytes(url)
        images = extract_embedded_images(pdf_bytes) or render_pages(pdf_bytes, dpi=150)
        images = images[:max_figures]

        records: List[FigureRecord] = []
        for image in images:
            page_text = image.page_text or ""
            records.append(FigureRecord(
                source=self.source,
                figure_id=f"{_slug(meeting)}_{doc_index}_fig{image.index:03d}",
                figure_type=FigureType.classify(meeting, page_text[:2000]),
                title=f"{meeting} poster {doc_index} figure {image.index}",
                context=page_text[:8000],
                url=url, image_url=url, image_ext=image.ext, image_bytes=image.data,
                ground_truth=extract_candidate_values(page_text),
                metadata={"meeting": meeting, "image_width": image.width,
                          "image_height": image.height},
            ))
        return records


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in value.lower()).strip("_") or "conf"
