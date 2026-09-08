"""Figure-corpus ingestion primitives.

Every source ingester subclasses :class:`BaseFigureIngester` and yields
:class:`FigureRecord` objects. A record is one figure: the image bytes, its
figure type, provenance, and any recoverable ground-truth values. Shared HTTP
handling lives in :class:`HttpClient`; persistence lives in :class:`FigureStorage`.

Self-contained: only the standard library is used, so a fresh clone can fetch
without installing anything. (PDF-based sources additionally shell out to
poppler; see :mod:`corpus.pdf_figures`.)
"""

from __future__ import annotations

import abc
import dataclasses
import hashlib
import json
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

logger = logging.getLogger("corpus.ingestion")

# Landing zone for the eval corpus. Kept under data/corpus so it does not collide
# with any company-keyed data a separate stage might write under data/.
DEFAULT_DATA_ROOT = Path("data") / "corpus"

# A descriptive User-Agent is polite and avoids default-client blocks. NCBI and
# SEC both ask for contact info in the UA.
DEFAULT_USER_AGENT = "perceptive-figure-corpus roshannarayan98@gmail.com"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def content_hash(data: bytes) -> str:
    """Stable SHA-256 hex digest, used to skip unchanged figures on re-runs."""
    return hashlib.sha256(data).hexdigest()


def safe_name(value: str) -> str:
    """Sanitize a value for use as a single path segment."""
    keep = [c if c.isalnum() or c in ("-", "_", ".") else "_" for c in (value or "").strip()]
    return "".join(keep) or "unknown"


class FigureType:
    """The six figure types the extractor handles, plus ``UNKNOWN``.

    String constants (not an enum) so they serialize to plain JSON. Use
    :meth:`classify` for a keyword-based guess from caption/title text.
    """

    KAPLAN_MEIER = "kaplan_meier"
    WATERFALL = "waterfall"
    FOREST = "forest"
    PK_LOGSCALE = "pk_logscale"
    TABLE = "table"
    SPIDER = "spider"
    UNKNOWN = "unknown"

    ALL = (KAPLAN_MEIER, WATERFALL, FOREST, PK_LOGSCALE, TABLE, SPIDER)

    # Ordered most-specific first so an early match wins (e.g. "swimmer" style
    # spider plots should not be caught by a generic "survival" rule).
    _PATTERNS = (
        (SPIDER, re.compile(r"\b(spider|swimmer|individual (patient|subject)|"
                            r"change in (tumou?r|target lesion))\b", re.I)),
        (WATERFALL, re.compile(r"\b(waterfall|best (percentage|%) change|"
                              r"best response|maximum reduction)\b", re.I)),
        (FOREST, re.compile(r"\b(forest plot|subgroup analys|hazard ratio.*subgroup|"
                           r"favou?rs)\b", re.I)),
        (PK_LOGSCALE, re.compile(r"\b(pharmacokinetic|concentration[-\s]?time|"
                               r"plasma concentration|dose[-\s]?response|"
                               r"pk profile|nM\b|ng/mL)\b", re.I)),
        (KAPLAN_MEIER, re.compile(r"\b(kaplan[-\s]?meier|progression[-\s]?free "
                                r"survival|overall survival|\bPFS\b|\bOS\b|"
                                r"survival curve|time[-\s]?to[-\s]?event)\b", re.I)),
        (TABLE, re.compile(r"\b(table|efficacy summary|baseline characteristic|"
                         r"adverse event|response rate)\b", re.I)),
    )

    @classmethod
    def classify(cls, *texts: str) -> str:
        """Best-effort figure type from caption/title text; ``UNKNOWN`` if unsure.

        A cheap first-pass label so harvested figures can be bucketed by type.
        The extractor's own router is the authority at extraction time; this is
        only to organize the corpus and to route ground-truth heuristics.
        """
        blob = " ".join(t for t in texts if t)
        for figure_type, pattern in cls._PATTERNS:
            if pattern.search(blob):
                return figure_type
        return cls.UNKNOWN


@dataclass
class GroundTruthValue:
    """One recovered true value for a figure, with how it was obtained.

    ``method`` records provenance of the *truth*, not of an extraction:
    ``"text_adjacent"`` (printed in the paper/label beside the figure),
    ``"structured"`` (from a results database), ``"annotated_dataset"`` (shipped
    with a labeled dataset), ``"regex_candidate"`` (auto-proposed from surrounding
    text, needs human confirmation), or ``"manual"`` (digitized by a person).
    ``verified`` gates whether a value may be used as a scoring reference.
    """

    quantity: str  # what it measures, e.g. "median PFS, treatment arm"
    value: Optional[str] = None  # kept as string to preserve "not reached", ranges, units
    unit: Optional[str] = None
    population_n: Optional[int] = None  # denominator, when the value is a proportion
    method: str = "text_adjacent"
    source_text: Optional[str] = None  # the sentence/cell the value came from
    verified: bool = False


@dataclass
class FigureRecord:
    """One figure in the corpus: the image plus its provenance and ground truth.

    ``image_bytes`` is the untouched figure image; :class:`FigureStorage` writes
    it to its own file and keeps this record metadata-only on disk. ``context``
    is the surrounding text (caption plus nearby results prose) that ground truth
    is recovered from. ``raw`` optionally holds an upstream payload for audit.
    """

    source: str  # e.g. "pmc", "fda", "cochraneforest"
    figure_id: str  # source-stable identifier
    figure_type: str = FigureType.UNKNOWN
    title: str = ""
    caption: str = ""
    context: str = ""  # captured surrounding text used to recover ground truth
    url: str = ""  # canonical human-facing URL for the source document
    image_url: str = ""  # direct URL the image came from, when applicable
    image_ext: str = "png"
    license: Optional[str] = None  # e.g. "CC BY", important for reuse
    published: Optional[str] = None
    retrieved_at: str = field(default_factory=utc_now_iso)
    ground_truth: List[GroundTruthValue] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    image_bytes: bytes = b""
    raw: str = ""

    @property
    def image_hash(self) -> str:
        return content_hash(self.image_bytes) if self.image_bytes else ""

    def to_record(self) -> Dict[str, Any]:
        """Serializable metadata record (excludes the bulky image and raw)."""
        record = dataclasses.asdict(self)
        record.pop("image_bytes", None)
        record.pop("raw", None)
        record["image_hash"] = self.image_hash
        record["image_file"] = f"image.{self.image_ext}"
        return record

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "FigureRecord":
        data = dict(record)
        data.pop("image_hash", None)
        data.pop("image_file", None)
        gts = data.pop("ground_truth", []) or []
        obj = cls(**data)
        obj.ground_truth = [GroundTruthValue(**g) if isinstance(g, dict) else g for g in gts]
        return obj


@dataclass
class IngestManifest:
    """Summary of one ingestion run for one source."""

    source: str
    started_at: str
    query: Optional[str] = None
    finished_at: Optional[str] = None
    figures: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def figure_count(self) -> int:
        return len(self.figures)

    def add(self, record: FigureRecord) -> None:
        self.figures.append({
            "figure_id": record.figure_id,
            "figure_type": record.figure_type,
            "title": record.title,
            "ground_truth_count": len(record.ground_truth),
            "verified_ground_truth": sum(1 for g in record.ground_truth if g.verified),
        })

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class HttpClient:
    """Minimal, dependency-free HTTP client with rate limiting and retries.

    ``min_interval`` enforces a floor between requests. ``get_json``,
    ``get_text`` and ``get_bytes`` are the accessors ingesters call.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval: float = 0.2,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    def get_bytes(self, url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
        merged = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        if headers:
            merged.update(headers)
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            req = urlrequest.Request(url, headers=merged)
            try:
                with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                    return _read_response(resp)
            except urlerror.HTTPError as exc:
                if exc.code != 429 and 400 <= exc.code < 500:
                    raise  # client error other than rate-limit will not improve
                last_error = exc
            except (socket.timeout, TimeoutError, urlerror.URLError) as exc:
                # socket.timeout is not a TimeoutError subclass before Python 3.10.
                last_error = exc
            backoff = min(2.0 ** attempt, 30.0)
            logger.warning("GET %s failed (attempt %d/%d): %s; retrying in %.1fs",
                           url, attempt, self.max_retries, last_error, backoff)
            time.sleep(backoff)
        raise RuntimeError(f"GET {url} failed after {self.max_retries} attempts: {last_error}")

    def get_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        return self.get_bytes(url, headers=headers).decode("utf-8", errors="replace")

    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        merged = {"Accept": "application/json"}
        if headers:
            merged.update(headers)
        return json.loads(self.get_bytes(url, headers=merged))


def _read_response(resp: Any) -> bytes:
    data = resp.read()
    encoding = resp.headers.get("Content-Encoding")
    if encoding == "gzip":
        import gzip
        return gzip.decompress(data)
    if encoding == "deflate":
        import zlib
        return zlib.decompress(data)
    return data


class FigureStorage:
    """Writes figures and manifests under ``<root>/<source>/<figure_id>/``.

    Layout::

        data/corpus/<source>/<figure_id>/record.json   # FigureRecord metadata + ground truth
        data/corpus/<source>/<figure_id>/image.<ext>    # the figure image
        data/corpus/<source>/<figure_id>/context.txt    # surrounding text (optional)
        data/corpus/<source>/<figure_id>/raw.txt         # upstream payload (optional)
        data/corpus/<source>/_manifest.json             # IngestManifest for the run

    ``is_unchanged`` lets an ingester skip a figure whose image already matches.
    """

    def __init__(self, root: Path = DEFAULT_DATA_ROOT) -> None:
        self.root = Path(root)

    def source_dir(self, source: str) -> Path:
        path = self.root / safe_name(source)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def figure_dir(self, source: str, figure_id: str) -> Path:
        path = self.source_dir(source) / safe_name(figure_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def is_unchanged(self, record: FigureRecord) -> bool:
        record_path = self.source_dir(record.source) / safe_name(record.figure_id) / "record.json"
        if not record_path.exists():
            return False
        try:
            existing = json.loads(record_path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return existing.get("image_hash") == record.image_hash

    def write_figure(self, record: FigureRecord) -> Path:
        directory = self.figure_dir(record.source, record.figure_id)
        (directory / "record.json").write_text(
            json.dumps(record.to_record(), indent=2, ensure_ascii=False)
        )
        if record.image_bytes:
            (directory / f"image.{record.image_ext}").write_bytes(record.image_bytes)
        if record.context:
            (directory / "context.txt").write_text(record.context)
        if record.raw:
            (directory / "raw.txt").write_text(record.raw)
        return directory

    def write_manifest(self, manifest: IngestManifest) -> Path:
        path = self.source_dir(manifest.source) / "_manifest.json"
        path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False))
        return path

    def load_figures(
        self, source: Optional[str] = None, figure_type: Optional[str] = None
    ) -> List[FigureRecord]:
        """Load stored figure records, optionally filtered by source and type.

        Image bytes are left on disk; the record's ``image_ext`` plus the figure
        directory locate the image file when a downstream stage needs it. This is
        the entry point the eval harness uses to read the corpus.
        """
        if not self.root.exists():
            return []
        source_dirs = (
            [self.root / safe_name(source)] if source
            else [p for p in self.root.iterdir() if p.is_dir()]
        )
        records: List[FigureRecord] = []
        for directory in source_dirs:
            if not directory.exists():
                continue
            for record_path in sorted(directory.glob("*/record.json")):
                try:
                    data = json.loads(record_path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                record = FigureRecord.from_record(data)
                if figure_type and record.figure_type != figure_type:
                    continue
                records.append(record)
        return records


# --- shared ground-truth heuristics ---------------------------------------

# Loose patterns that PROPOSE candidate ground-truth values from surrounding
# text. These are deliberately conservative and always land as unverified
# "regex_candidate" values: a human or a verification pass confirms them before
# they score anything. Reporting an unconfirmed number as truth would be exactly
# the "affirming what it is shown" failure the brief warns against.
_HR_CI = re.compile(
    r"\b(?:HR|hazard ratio)\b[^0-9]{0,15}(?P<hr>\d\.\d{1,3})"
    r"[^0-9]{0,15}(?:95%?\s*CI[^0-9]{0,6})?(?P<lo>\d\.\d{1,3})"
    r"\s*(?:[-–—to,]+)\s*(?P<hi>\d\.\d{1,3})",
    re.I,
)
_MEDIAN = re.compile(
    r"\bmedian\b[^.]{0,40}?(?P<val>\d{1,3}(?:\.\d)?)\s*(?P<unit>months?|weeks?|mo\b)",
    re.I,
)
_PROPORTION = re.compile(
    r"(?P<pct>\d{1,3}(?:\.\d)?)\s*%\s*\(?\s*(?P<num>\d{1,4})\s*/\s*(?P<den>\d{1,4})\s*\)?",
)


def extract_candidate_values(text: str, limit: int = 12) -> List[GroundTruthValue]:
    """Propose unverified ground-truth candidates from surrounding text.

    Catches the three shapes most common beside oncology figures: hazard ratios
    with a CI, medians with a time unit, and ``pct% (n/N)`` proportions (which
    also recover the denominator). Everything returned is ``verified=False``.
    """
    if not text:
        return []
    out: List[GroundTruthValue] = []

    for m in _HR_CI.finditer(text):
        out.append(GroundTruthValue(
            quantity="hazard ratio (candidate)",
            value=f"{m.group('hr')} (95% CI {m.group('lo')}-{m.group('hi')})",
            method="regex_candidate",
            source_text=_snippet(text, m.start(), m.end()),
        ))
    for m in _MEDIAN.finditer(text):
        out.append(GroundTruthValue(
            quantity="median (candidate)",
            value=m.group("val"),
            unit=m.group("unit").rstrip("."),
            method="regex_candidate",
            source_text=_snippet(text, m.start(), m.end()),
        ))
    for m in _PROPORTION.finditer(text):
        num, den = int(m.group("num")), int(m.group("den"))
        if den == 0 or num > den:
            continue  # not a real proportion
        out.append(GroundTruthValue(
            quantity="proportion (candidate)",
            value=f"{m.group('pct')}% ({num}/{den})",
            population_n=den,
            method="regex_candidate",
            source_text=_snippet(text, m.start(), m.end()),
        ))
        if len(out) >= limit:
            break
    return out[:limit]


def _snippet(text: str, start: int, end: int, pad: int = 40) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - pad):min(len(text), end + pad)]).strip()


class BaseFigureIngester(abc.ABC):
    """Base class for all figure-source ingesters.

    Subclasses implement :meth:`fetch`, yielding :class:`FigureRecord` objects
    for a query. :meth:`run` orchestrates: build a manifest, persist each figure
    (skipping unchanged ones unless ``force``), record errors, write the manifest.
    Subclasses set :attr:`source`.
    """

    source: str = ""

    def __init__(
        self,
        http: Optional[HttpClient] = None,
        storage: Optional[FigureStorage] = None,
    ) -> None:
        if not self.source:
            raise ValueError(f"{type(self).__name__} must define a non-empty 'source'")
        self.http = http or HttpClient()
        self.storage = storage or FigureStorage()

    @abc.abstractmethod
    def fetch(self, query: str, **options: Any) -> Iterable[FigureRecord]:
        """Fetch figure records for ``query``. Implemented by each source."""
        raise NotImplementedError

    def run(self, query: str, force: bool = False, **options: Any) -> IngestManifest:
        """Fetch, persist, and summarize an ingestion run."""
        logger.info("Ingesting %s for query %r", self.source, query)
        manifest = IngestManifest(source=self.source, started_at=utc_now_iso(), query=query)
        try:
            records = self.fetch(query, **options)
        except Exception as exc:  # noqa: BLE001 - surface fetch failure in manifest
            logger.exception("fetch failed for %s", self.source)
            manifest.errors.append(f"fetch failed: {exc}")
            manifest.finished_at = utc_now_iso()
            self.storage.write_manifest(manifest)
            return manifest

        for record in records:
            try:
                if not force and self.storage.is_unchanged(record):
                    logger.debug("unchanged, skipping %s", record.figure_id)
                else:
                    self.storage.write_figure(record)
                manifest.add(record)
            except Exception as exc:  # noqa: BLE001 - one bad figure should not abort
                logger.exception("failed to write %s", record.figure_id)
                manifest.errors.append(f"{record.figure_id}: {exc}")

        manifest.finished_at = utc_now_iso()
        self.storage.write_manifest(manifest)
        logger.info("Ingested %d figures for %s (%d errors)",
                    manifest.figure_count, self.source, len(manifest.errors))
        return manifest
