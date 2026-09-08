"""Extract figure images and text from PDFs, via poppler.

FDA briefing documents and SEC investor decks are PDFs, and their figures are the
data we want. Rather than add a PDF library dependency, this shells out to
poppler (``pdfimages``, ``pdftotext``, ``pdftoppm``), which the repo already
relies on for the Stage 1 figure extraction. If poppler is absent, the callers
degrade to metadata-only rather than crashing.

Two extraction modes:

* :func:`extract_embedded_images` pulls the raster objects a PDF embeds. This is
  lossless and ideal when each figure is one embedded image (the common case for
  slide decks exported to PDF).
* :func:`render_pages` rasterizes whole pages at a chosen DPI. Use it as a
  fallback when figures are drawn as vector graphics (no embedded raster to pull).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("corpus.pdf")

# Ignore extracted images smaller than this on either side: page rules, logos,
# and icons, not figures.
MIN_IMAGE_DIM = 200


def poppler_available() -> bool:
    """True if the poppler tools this module needs are on PATH."""
    return all(shutil.which(tool) for tool in ("pdfimages", "pdftotext"))


def _require_poppler() -> None:
    if not poppler_available():
        raise RuntimeError(
            "poppler not found on PATH (need pdfimages, pdftotext). "
            "Install it (macOS: `brew install poppler`) or use a source that "
            "does not read PDFs."
        )


@dataclass
class ExtractedImage:
    """One image pulled from a PDF, with enough context to make a FigureRecord."""

    index: int  # 0-based order within the document
    data: bytes
    ext: str  # "png", "ppm", ...
    width: Optional[int] = None
    height: Optional[int] = None
    page_text: str = ""  # text of the page it was found on, for ground truth


def pdf_page_count(pdf_bytes: bytes) -> int:
    """Number of pages, via ``pdfinfo`` when present (0 if it cannot be read)."""
    if not shutil.which("pdfinfo"):
        return 0
    with _temp_pdf(pdf_bytes) as pdf_path:
        try:
            out = subprocess.run(
                ["pdfinfo", str(pdf_path)],
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout
        except (subprocess.SubprocessError, OSError):
            return 0
    for line in out.splitlines():
        if line.lower().startswith("pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def pdf_to_text(pdf_bytes: bytes, first: Optional[int] = None, last: Optional[int] = None) -> str:
    """Whole-document (or page-range) plain text, via ``pdftotext``.

    Used to recover ground-truth numbers printed near a figure. ``first``/``last``
    are 1-based inclusive page bounds.
    """
    _require_poppler()
    with _temp_pdf(pdf_bytes) as pdf_path:
        cmd = ["pdftotext", "-layout"]
        if first is not None:
            cmd += ["-f", str(first)]
        if last is not None:
            cmd += ["-l", str(last)]
        cmd += [str(pdf_path), "-"]
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, check=True,
            ).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("pdftotext failed: %s", exc)
            return ""


def extract_embedded_images(
    pdf_bytes: bytes,
    min_dim: int = MIN_IMAGE_DIM,
    attach_page_text: bool = True,
    page_window: int = 1,
) -> List[ExtractedImage]:
    """Pull embedded raster images from a PDF, largest-first isn't guaranteed.

    Small images (below ``min_dim`` on a side) are dropped as non-figures, and so
    are soft-mask (``smask``) objects: those are the alpha channels of adjacent
    images, not figures in their own right, yet they share their parent's
    dimensions and would otherwise slip past the size filter as noise.

    When ``attach_page_text`` is set, each image carries the text of the page it
    was found on (mapped via ``pdfimages -list``), plus ``page_window`` pages on
    either side. The figure's own page is what classification keys on, while the
    surrounding pages catch results prose (hazard ratios, medians) that oncology
    reviews routinely print a page away from the plot. If the page map cannot be
    built the code degrades to attaching the whole-document text, which is coarser
    but still lets ground-truth recovery run.
    """
    _require_poppler()
    images: List[ExtractedImage] = []
    with _temp_pdf(pdf_bytes) as pdf_path:
        with tempfile.TemporaryDirectory() as out_dir:
            prefix = Path(out_dir) / "img"
            try:
                subprocess.run(
                    ["pdfimages", "-png", str(pdf_path), str(prefix)],
                    capture_output=True, timeout=300, check=True,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning("pdfimages failed: %s", exc)
                return images

            # Map each emitted image number to its (type, page) so we can drop
            # soft masks and attach the right page's text. Falls back to an empty
            # map (whole-document text) if -list is unavailable or unparseable.
            image_meta = _list_embedded_images(pdf_path)
            whole_doc_text = ""  # lazily filled only if we lack a page map
            page_text_cache: dict = {}

            for path in sorted(Path(out_dir).glob("img-*.png")):
                num = _image_number(path)
                meta = image_meta.get(num) if num is not None else None
                if meta and meta[0] == "smask":
                    continue  # alpha mask of another image, not a figure

                data = path.read_bytes()
                width, height = _png_dimensions(data)
                if width and height and (width < min_dim or height < min_dim):
                    continue

                page_text = ""
                if attach_page_text:
                    page = meta[1] if meta else None
                    if page is not None:
                        # Own page first so classification (which reads only the
                        # head of this text) keys on the figure's own caption;
                        # neighbours follow to catch nearby results prose.
                        neighbours = [page] + [
                            p for w in range(1, page_window + 1) for p in (page - w, page + w)
                        ]
                        parts = []
                        for p in neighbours:
                            if p < 1:
                                continue
                            if p not in page_text_cache:
                                page_text_cache[p] = pdf_to_text(
                                    pdf_bytes, first=p, last=p
                                )
                            if page_text_cache[p]:
                                parts.append(page_text_cache[p])
                        page_text = "\n".join(parts)
                    else:
                        if not whole_doc_text:
                            whole_doc_text = pdf_to_text(pdf_bytes)
                        page_text = whole_doc_text

                images.append(ExtractedImage(
                    index=num if num is not None else len(images),
                    data=data, ext="png",
                    width=width, height=height, page_text=page_text,
                ))
    return images


def _image_number(path: Path) -> Optional[int]:
    """The image index poppler encoded in an ``img-<n>.png`` filename."""
    match = re.search(r"-(\d+)\.png$", path.name)
    return int(match.group(1)) if match else None


def _list_embedded_images(pdf_path: Path) -> "dict[int, tuple[str, int]]":
    """Map each embedded-image number to ``(type, page)`` via ``pdfimages -list``.

    ``type`` is poppler's object kind (``image``, ``smask``, ``stencil``, ...) and
    ``page`` is 1-based. Returns an empty dict if the listing cannot be produced,
    so callers can degrade to whole-document text.
    """
    try:
        out = subprocess.run(
            ["pdfimages", "-list", str(pdf_path)],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("pdfimages -list failed: %s", exc)
        return {}
    meta: "dict[int, tuple[str, int]]" = {}
    for line in out.splitlines():
        parts = line.split()
        # Rows look like: <page> <num> <type> <width> <height> ...
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        page, num, kind = int(parts[0]), int(parts[1]), parts[2]
        meta[num] = (kind, page)
    return meta


def render_pages(
    pdf_bytes: bytes, dpi: int = 150, first: Optional[int] = None, last: Optional[int] = None
) -> List[ExtractedImage]:
    """Rasterize whole pages to PNG via ``pdftoppm`` (fallback for vector figures)."""
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm not found on PATH; cannot render pages.")
    images: List[ExtractedImage] = []
    with _temp_pdf(pdf_bytes) as pdf_path:
        with tempfile.TemporaryDirectory() as out_dir:
            prefix = Path(out_dir) / "page"
            cmd = ["pdftoppm", "-png", "-r", str(dpi)]
            if first is not None:
                cmd += ["-f", str(first)]
            if last is not None:
                cmd += ["-l", str(last)]
            cmd += [str(pdf_path), str(prefix)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=300, check=True)
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning("pdftoppm failed: %s", exc)
                return images
            for index, path in enumerate(sorted(Path(out_dir).glob("page-*.png"))):
                data = path.read_bytes()
                width, height = _png_dimensions(data)
                page_no = (first or 1) + index
                images.append(ExtractedImage(
                    index=index, data=data, ext="png",
                    width=width, height=height,
                    page_text=pdf_to_text(pdf_bytes, first=page_no, last=page_no),
                ))
    return images


def _png_dimensions(data: bytes) -> "tuple[Optional[int], Optional[int]]":
    """Read a PNG's width/height from its IHDR chunk without an image library."""
    # PNG signature (8 bytes) + IHDR length (4) + "IHDR" (4) then width, height.
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


class _temp_pdf:
    """Context manager writing PDF bytes to a temp file poppler can read."""

    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self._path: Optional[Path] = None

    def __enter__(self) -> Path:
        fd = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        fd.write(self._pdf_bytes)
        fd.close()
        self._path = Path(fd.name)
        return self._path

    def __exit__(self, *exc: object) -> None:
        if self._path and self._path.exists():
            self._path.unlink()
