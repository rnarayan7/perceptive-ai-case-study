"""PubMed Central open-access ingestion.

The highest-yield source: an open-access paper carries the figure and its true
numbers in one document (curves in the figure, medians/HRs/rates in the results
text and caption). This ingester:

1. Searches Europe PMC for open-access articles matching a query.
2. For each hit, downloads the article's open-access files (JATS XML plus the
   figure images) from the PMC Cloud Service open-data S3 bucket.
3. Parses the XML ``<fig>`` elements, pairs each caption with its image file,
   classifies the figure type, and proposes candidate ground-truth values from
   the caption.

The legacy NCBI OA Web Service (``oa.fcgi``) and the per-article FTP tarballs it
pointed at were retired in August 2026 as part of the PMC Article Dataset
Distribution changes. The open-access subset is now published in the
world-readable S3 bucket ``pmc-oa-opendata`` (https://registry.opendata.aws/
ncbi-pmc), where each article version is a key prefix ``PMC<id>.<version>/``
holding the XML, a plain-text version, JSON metadata, and (license permitting)
the PDF and figure images. We resolve an article by listing that prefix over
the bucket's anonymous HTTPS ListObjectsV2 endpoint.

Only the standard library is used (urllib, xml, re).

Licensing: the query is restricted to the open-access subset, and each figure
records the article ``license`` when Europe PMC reports it. Filter on
``license`` downstream before any commercial use; the non-commercial subset is
fine for internal evaluation.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional

from corpus.base import (
    BaseFigureIngester,
    FigureRecord,
    FigureType,
    extract_candidate_values,
)

logger = logging.getLogger("corpus.pmc")

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
# PMC Cloud Service open-data bucket; anonymous HTTPS access, no key.
S3_BUCKET_URL = "https://pmc-oa-opendata.s3.amazonaws.com"
ARTICLE_URL = "https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"

DEFAULT_LIMIT = 25
DEFAULT_PAGE_SIZE = 25
# Image members worth keeping; skip thumbnails and equations.
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff")


class PmcIngester(BaseFigureIngester):
    """Ingest figures from open-access PubMed Central articles.

    ``fetch`` takes a search ``query`` (Europe PMC query syntax, e.g.
    ``"kaplan meier chondrosarcoma"``) and returns one :class:`FigureRecord` per
    figure across the matched articles.

    Options:

    ``limit``
        Max number of *articles* to pull (default 25). Each yields several figures.
    ``figure_types``
        Optional iterable of :class:`FigureType` values to keep; others are
        dropped. Useful to build a per-type slice (e.g. only ``forest``).
    ``page_size``
        Articles per search page (default 25).
    """

    source = "pmc"

    def fetch(self, query: str, **options: Any) -> Iterable[FigureRecord]:
        limit = int(options.get("limit") or DEFAULT_LIMIT)
        page_size = int(options.get("page_size") or DEFAULT_PAGE_SIZE)
        # A single ``--option figure_types=forest`` arrives as a bare string; wrap
        # it so we build a set of type names, not a set of its characters.
        raw_types = options.get("figure_types") or ()
        if isinstance(raw_types, str):
            raw_types = [raw_types]
        wanted_types = set(raw_types)

        articles = self._search(query, limit=limit, page_size=page_size)
        logger.info("PMC: %d open-access articles for %r", len(articles), query)

        records: List[FigureRecord] = []
        for article in articles:
            pmcid = article.get("pmcid")
            if not pmcid:
                continue
            try:
                records.extend(self._figures_from_article(article, wanted_types))
            except Exception as exc:  # noqa: BLE001 - skip a bad article, keep going
                logger.warning("PMC: failed on %s: %s", pmcid, exc)
        return records

    # -- search -------------------------------------------------------------

    def _search(self, query: str, limit: int, page_size: int) -> List[Dict[str, Any]]:
        """Search Europe PMC's open-access subset; return core article dicts."""
        import urllib.parse

        results: List[Dict[str, Any]] = []
        cursor = "*"
        full_query = f"({query}) AND OPEN_ACCESS:y AND IN_EPMC:y"
        while len(results) < limit:
            params = {
                "query": full_query,
                "format": "json",
                "resultType": "core",
                "pageSize": str(min(page_size, limit - len(results))),
                "cursorMark": cursor,
            }
            url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
            payload = self.http.get_json(url)
            hits = (payload.get("resultList") or {}).get("result") or []
            if not hits:
                break
            for hit in hits:
                results.append({
                    "pmcid": hit.get("pmcid"),
                    "pmid": hit.get("pmid"),
                    "title": hit.get("title", ""),
                    "license": hit.get("license"),
                    "published": hit.get("firstPublicationDate"),
                    "journal": (hit.get("journalInfo") or {}).get("journal", {}).get("title"),
                })
            next_cursor = payload.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return results[:limit]

    # -- OA package + figure parsing ---------------------------------------

    def _figures_from_article(
        self, article: Dict[str, Any], wanted_types: set
    ) -> List[FigureRecord]:
        pmcid = article["pmcid"]
        package = self._download_oa_package(pmcid)
        if package is None:
            return []
        images, xml_bytes = package
        if not xml_bytes:
            return []
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return []
        body_text = self._body_text(root)

        records: List[FigureRecord] = []
        for fig in self._parse_figures(root):
            image = self._match_image(fig["graphic"], images)
            if image is None:
                continue
            data, ext = image
            caption = fig["caption"]
            # Skip caption-less floats (graphical abstracts, decorative images):
            # they carry no text to classify or to recover ground truth from.
            if len(caption) < 20:
                continue
            figure_type = FigureType.classify(fig["label"], caption, article.get("title", ""))
            if wanted_types and figure_type not in wanted_types:
                continue
            # Ground truth lives in the results prose ("median PFS was 9.1 months",
            # "HR 0.62"), not usually in the caption. Pair the caption with the body
            # sentences that reference this figure so the candidate extractor has the
            # real numbers to propose from.
            context = self._figure_context(caption, fig["label"], body_text)
            record = FigureRecord(
                source=self.source,
                figure_id=f"{pmcid}_{fig['id'] or fig['graphic']}",
                figure_type=figure_type,
                title=article.get("title", ""),
                caption=caption,
                context=context,
                url=ARTICLE_URL.format(pmcid=pmcid),
                image_ext=ext,
                license=article.get("license"),
                published=article.get("published"),
                image_bytes=data,
                ground_truth=extract_candidate_values(context),
                metadata={
                    "pmcid": pmcid,
                    "pmid": article.get("pmid"),
                    "journal": article.get("journal"),
                    "fig_label": fig["label"],
                },
            )
            records.append(record)
        return records

    def _download_oa_package(
        self, pmcid: str
    ) -> "Optional[tuple[Dict[str, bytes], Optional[bytes]]]":
        """Resolve and download an article's OA files from S3; return (images, xml).

        ``images`` maps a lowercased filename stem to its bytes; ``xml`` is the
        article's JATS XML. Returns ``None`` when the article has no objects in
        the open-data bucket (e.g. it is open-access for reading but not part of
        the redistributable subset, so no images/XML are published there).
        """
        keys = self._list_article_keys(pmcid)
        if not keys:
            logger.debug("PMC: no OA objects for %s", pmcid)
            return None
        prefix = self._latest_version_prefix(keys)
        version_keys = [k for k in keys if k.startswith(prefix)]

        xml_key = next(
            (k for k in version_keys if k.lower().endswith((".nxml", ".xml"))), None
        )
        xml_bytes: Optional[bytes] = (
            self.http.get_bytes(self._object_url(xml_key)) if xml_key else None
        )

        images: Dict[str, bytes] = {}
        for key in version_keys:
            if key.lower().endswith(_IMAGE_SUFFIXES):
                images[_stem(key)] = self.http.get_bytes(self._object_url(key))
        return images, xml_bytes

    def _list_article_keys(self, pmcid: str) -> List[str]:
        """List every object key for ``pmcid`` in the open-data bucket.

        Uses the anonymous S3 ListObjectsV2 HTTPS endpoint. The trailing dot on
        the prefix (``PMC123.``) scopes the listing to versions of this exact
        article rather than any PMCID that merely starts with the same digits.
        """
        import urllib.parse

        keys: List[str] = []
        token: Optional[str] = None
        while True:
            params = {"list-type": "2", "prefix": f"{pmcid}."}
            if token:
                params["continuation-token"] = token
            url = f"{S3_BUCKET_URL}/?{urllib.parse.urlencode(params)}"
            body = self.http.get_text(url)
            keys.extend(html.unescape(k) for k in re.findall(r"<Key>([^<]+)</Key>", body))
            if "<IsTruncated>true</IsTruncated>" not in body:
                break
            m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", body)
            if not m:
                break
            token = html.unescape(m.group(1))
        return keys

    @staticmethod
    def _latest_version_prefix(keys: List[str]) -> str:
        """Pick the ``PMC<id>.<version>/`` prefix with the highest version number.

        Keys look like ``PMC13380284.1/CMAR-18-621105-g0001.jpg``; the version
        prefix is everything up to and including the first ``/``.
        """
        prefixes = {k.split("/", 1)[0] + "/" for k in keys if "/" in k}

        def version(prefix: str) -> int:
            try:
                return int(prefix.rstrip("/").rsplit(".", 1)[-1])
            except ValueError:
                return -1

        return max(prefixes, key=version) if prefixes else ""

    @staticmethod
    def _object_url(key: str) -> str:
        import urllib.parse

        return f"{S3_BUCKET_URL}/{urllib.parse.quote(key, safe='/')}"

    @staticmethod
    def _parse_figures(root: ET.Element) -> List[Dict[str, str]]:
        """Extract ``<fig>`` elements as {id, label, caption, graphic}."""
        figures: List[Dict[str, str]] = []
        for fig in root.iter():
            if _localname(fig.tag) != "fig":
                continue
            fig_id = fig.get("id", "")
            label = ""
            caption_parts: List[str] = []
            graphic = ""
            for child in fig.iter():
                tag = _localname(child.tag)
                if tag == "label" and child.text:
                    label = "".join(child.itertext()).strip()
                elif tag == "caption":
                    caption_parts.append(" ".join(child.itertext()).strip())
                elif tag == "graphic":
                    href = _xlink_href(child)
                    if href:
                        graphic = href
            figures.append({
                "id": fig_id,
                "label": label,
                "caption": " ".join(p for p in caption_parts if p).strip(),
                "graphic": graphic,
            })
        return figures

    @staticmethod
    def _body_text(root: ET.Element) -> str:
        """Flatten the article ``<body>`` prose into one whitespace-normalized string.

        Figures, tables and their captions are dropped so the text is the running
        results/discussion prose that reports the actual numbers. Falls back to the
        whole document when no ``<body>`` is present.
        """
        bodies = [el for el in root.iter() if _localname(el.tag) == "body"]
        scope = bodies[0] if bodies else root
        skip = {"fig", "table-wrap", "table", "caption", "label"}
        parts: List[str] = []

        def walk(el: ET.Element) -> None:
            if _localname(el.tag) in skip:
                return
            if el.text:
                parts.append(el.text)
            for child in el:
                walk(child)
                if child.tail:
                    parts.append(child.tail)

        walk(scope)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    @staticmethod
    def _figure_context(caption: str, label: str, body_text: str, max_len: int = 1500) -> str:
        """Caption plus body sentences that reference this figure.

        Ground-truth numbers (medians, hazard ratios, response rates) sit in the
        results prose next to a "Figure N" mention, not in the caption. We pull the
        figure number from ``label`` and keep sentences that cite it, giving the
        candidate extractor the surrounding text without dragging in the whole body.
        Returns the caption alone when the figure has no usable number or no hits.
        """
        num = re.search(r"\d+", label or "")
        if not num or not body_text:
            return caption
        n = int(num.group(0))
        sentences = re.split(r"(?<=[.!?])\s+", body_text)
        hits = [s.strip() for s in sentences if _cites_figure(s, n)]
        if not hits:
            return caption
        context = caption + " " + " ".join(hits)
        return context[:max_len].strip()

    @staticmethod
    def _match_image(
        graphic_href: str, images: Dict[str, bytes]
    ) -> "Optional[tuple[bytes, str]]":
        """Find the image file for a graphic href (which usually lacks an ext)."""
        if not graphic_href or not images:
            return None
        stem = _stem(graphic_href)
        if stem in images:
            return images[stem], "jpg"  # PMC figure rasters are typically jpg
        # Some hrefs carry an extension or a path; retry on the bare stem.
        for key, data in images.items():
            if key.endswith(stem) or stem.endswith(key):
                return data, "jpg"
        return None


# A figure citation and the number-group after it: "Fig 3", "Figures 1-4",
# "Figures 1 and 2", "Figures 1, 2 and 3", "Figs 1 to 3". Restricted to digits and
# range/list separators so it cannot run past the citation into ordinary prose.
_FIG_CITE = re.compile(
    r"\bfig(?:ure)?s?\.?\s*(\d+(?:\s*(?:[-–—]|,|and|to|&)\s*\d+)*)", re.I
)
_FIG_RANGE = re.compile(r"(\d+)\s*(?:[-–—]|to)\s*(\d+)")


def _cites_figure(sentence: str, n: int) -> bool:
    """Whether ``sentence`` cites figure number ``n``, honoring ranges and lists."""
    for m in _FIG_CITE.finditer(sentence):
        block = m.group(1)
        if any(int(a) <= n <= int(b) for a, b in _FIG_RANGE.findall(block)):
            return True
        if any(int(x) == n for x in re.findall(r"\d+", block)):
            return True
    return False


def _stem(name: str) -> str:
    base = name.rsplit("/", 1)[-1].lower()
    for suffix in _IMAGE_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _localname(tag: str) -> str:
    """Strip an XML namespace, returning the bare local tag name."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xlink_href(element: ET.Element) -> str:
    """Return an element's xlink:href regardless of namespace prefix."""
    for key, value in element.attrib.items():
        if _localname(key) == "href":
            return value
    return ""
