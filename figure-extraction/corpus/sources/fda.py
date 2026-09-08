"""FDA documents (ODAC briefing books, labels) as a figure source.

FDA advisory-committee briefing documents and approval packages are public PDFs
dense with regulator-grade survival curves, forest plots, and efficacy tables,
and the same PDF prints the numbers. This ingester takes document URLs, fetches
each PDF, extracts its figure images (via poppler; see :mod:`corpus.pdf_figures`),
and attaches the document text so ground-truth candidates can be recovered.

The open problem this does *not* solve is discovery: FDA hosts these under opaque
``media/<id>/download`` paths with no clean listing API, so the URLs are supplied
rather than crawled. Give it URLs via ``query`` (comma-separated) or ``urls=``.
That is an honest boundary: the ingestion is real, the harvesting of *which*
documents is a manual or separately-scripted step.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

from corpus.base import (
    BaseFigureIngester,
    FigureRecord,
    FigureType,
    extract_candidate_values,
)
from corpus.pdf_figures import (
    extract_embedded_images,
    poppler_available,
    render_pages,
)

logger = logging.getLogger("corpus.fda")

DEFAULT_MAX_FIGURES_PER_DOC = 30


class FdaDocumentIngester(BaseFigureIngester):
    """Extract figures from supplied FDA PDF document URLs.

    ``fetch`` takes one or more PDF URLs (comma-separated ``query`` or ``urls``
    option) and returns one :class:`FigureRecord` per extracted figure image.

    Options:

    ``urls``
        Iterable of PDF URLs (alternative to a comma-separated ``query``).
    ``doc_label``
        Human label for the document, used in titles and ids.
    ``max_figures``
        Cap on figures per document (default 30).
    ``render_fallback``
        If ``True`` and a PDF embeds no usable rasters, rasterize pages instead
        (for vector-drawn figures). Default ``True``.
    """

    source = "fda"

    def fetch(self, query: str, **options: Any) -> Iterable[FigureRecord]:
        if not poppler_available():
            raise RuntimeError(
                "FDA ingestion needs poppler (pdfimages/pdftotext) on PATH."
            )
        urls = list(options.get("urls") or [])
        urls += [u.strip() for u in (query or "").split(",") if u.strip()]
        if not urls:
            raise RuntimeError("No document URLs. Pass query=<url[,url...]> or urls=[...].")

        max_figures = int(options.get("max_figures") or DEFAULT_MAX_FIGURES_PER_DOC)
        render_fallback = options.get("render_fallback", True)
        base_label = options.get("doc_label")

        records: List[FigureRecord] = []
        for doc_index, url in enumerate(urls):
            label = base_label or f"fda_doc_{doc_index + 1}"
            try:
                records.extend(
                    self._figures_from_pdf(url, label, max_figures, render_fallback)
                )
            except Exception as exc:  # noqa: BLE001 - skip a bad doc, keep going
                logger.warning("fda: failed on %s: %s", url, exc)
        return records

    def _figures_from_pdf(
        self, url: str, label: str, max_figures: int, render_fallback: bool
    ) -> List[FigureRecord]:
        pdf_bytes = self.http.get_bytes(url)
        images = extract_embedded_images(pdf_bytes)
        if not images and render_fallback:
            logger.info("fda: no embedded rasters in %s; rendering pages", url)
            images = render_pages(pdf_bytes, dpi=150)
        images = images[:max_figures]

        records: List[FigureRecord] = []
        for image in images:
            page_text = image.page_text or ""
            figure_type = FigureType.classify(label, page_text[:2000])
            records.append(FigureRecord(
                source=self.source,
                figure_id=f"{label}_fig{image.index:03d}",
                figure_type=figure_type,
                title=f"{label} figure {image.index}",
                context=page_text[:8000],
                url=url,
                image_url=url,
                image_ext=image.ext,
                image_bytes=image.data,
                ground_truth=extract_candidate_values(page_text),
                metadata={
                    "doc_label": label,
                    "image_width": image.width,
                    "image_height": image.height,
                    "extraction": "embedded" if not render_fallback or image.ext == "png" else "rendered",
                },
            ))
        return records
