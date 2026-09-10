"""Shared ingestion primitives.

Every source ingester subclasses :class:`BaseIngester` and returns an
:class:`IngestManifest`. Shared HTTP handling (User-Agent, rate limiting, retries)
lives in :class:`HttpClient`; writing normalized documents to disk lives in
:class:`Storage`. Source modules should not re-implement any of this.
"""

from __future__ import annotations

import abc
import dataclasses
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

logger = logging.getLogger("memo.ingestion")

# Landing zone for fetched data. Overridable per ingester via Storage(root=...).
DEFAULT_DATA_ROOT = Path("data")

# SEC asks for a descriptive User-Agent with contact info. ClinicalTrials.gov has
# no such requirement but a real UA is polite and avoids default-client blocks.
DEFAULT_USER_AGENT = "perceptive-memo-research roshannarayan98@gmail.com"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (used for run timestamps)."""
    return datetime.now(timezone.utc).isoformat()


_YEAR = re.compile(r"^(\d{4})$")
_YEAR_RANGE = re.compile(r"^(\d{4})-(\d{4})$")
_YEAR_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def normalize_date(value: Optional[str]) -> Optional[str]:
    """Normalize a document date to sortable ``YYYY-MM-DD``, or None.

    Date filtering compares dates lexicographically, so a bare year mis-sorts:
    ``"2024" < "2024-01-01"`` is True, which would wrongly exclude a 2024 document
    from a ``since="2024-01-01"`` window. Padding bare years/months (and collapsing a
    ``YYYY-YYYY`` range to its start year) makes every source comparable. Values that
    are already ``YYYY-MM-DD`` (or anything unrecognized) pass through unchanged.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if _YEAR.match(s):
        return f"{s}-01-01"
    m = _YEAR_RANGE.match(s)
    if m:
        return f"{m.group(1)}-01-01"
    if _YEAR_MONTH.match(s):
        return f"{s}-01"
    return s


def content_hash(data: bytes) -> str:
    """Stable SHA-256 hex digest, used to skip unchanged documents on re-runs."""
    return hashlib.sha256(data).hexdigest()


@dataclass
class Document:
    """One normalized unit of ingested content.

    A ``Document`` is the common record every source produces, whatever the shape
    of its upstream API. ``raw`` holds the untouched payload (JSON text, HTML, etc.)
    so nothing is lost; ``text`` is a best-effort plain-text rendering for indexing;
    ``metadata`` carries source-specific fields (form type, NCT id, dates, urls).
    """

    company: str  # ticker or canonical company key this document belongs to
    source: str  # e.g. "edgar", "clinicaltrials"
    doc_type: str  # source-specific kind, e.g. "10-K", "study"
    doc_id: str  # source-stable identifier, e.g. accession number or NCT id
    title: str
    url: str
    published: Optional[str] = None  # ISO date when known
    retrieved_at: str = field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    raw: str = ""

    def __post_init__(self) -> None:
        # Normalize the date on the way in so every source (and anything reloaded
        # from disk via from_record) is date-filterable and sorts correctly.
        self.published = normalize_date(self.published)

    @property
    def hash(self) -> str:
        """Hash of the raw payload, for change detection."""
        return content_hash(self.raw.encode("utf-8"))

    def to_record(self) -> Dict[str, Any]:
        """Serializable metadata record (excludes bulky ``raw`` and ``text``).

        ``text`` and ``raw`` are persisted as sibling files by :class:`Storage`,
        so the JSON record stays small and metadata-only.
        """
        record = dataclasses.asdict(self)
        record.pop("raw", None)
        record.pop("text", None)
        record["hash"] = self.hash
        return record

    @classmethod
    def from_record(cls, record: Dict[str, Any], text: str = "", raw: str = "") -> "Document":
        """Rebuild a ``Document`` from a stored record plus its text/raw payloads."""
        data = dict(record)
        data.pop("hash", None)
        return cls(text=text, raw=raw, **data)


@dataclass
class IngestManifest:
    """Summary of one ingestion run for one source and company."""

    company: str
    source: str
    started_at: str
    finished_at: Optional[str] = None
    documents: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def document_count(self) -> int:
        return len(self.documents)

    def add(self, document: Document) -> None:
        self.documents.append(document.to_record())

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class HttpClient:
    """Minimal, dependency-free HTTP client with rate limiting and retries.

    Uses the standard library so a fresh clone needs no extra packages to fetch.
    ``min_interval`` enforces a floor between requests (SEC allows ~10 req/s, so the
    default 0.15s is safe). ``get_json`` and ``get_text`` are the two accessors
    ingesters should call.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval: float = 0.15,
        max_retries: int = 3,
        timeout: float = 30.0,
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
        """GET a URL, returning raw bytes. Retries transient failures with backoff."""
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
                # 4xx (except 429) will not improve on retry; fail fast.
                if exc.code != 429 and 400 <= exc.code < 500:
                    raise
                last_error = exc
            except (urlerror.URLError, TimeoutError) as exc:
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
    """Read a urlopen response, transparently gunzipping when needed."""
    data = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        import gzip
        data = gzip.decompress(data)
    elif resp.headers.get("Content-Encoding") == "deflate":
        import zlib
        data = zlib.decompress(data)
    return data


class Storage:
    """Writes documents and manifests to ``<root>/<company>/<source>/``.

    Layout::

        data/<company>/<source>/<doc_id>.json      # normalized Document record
        data/<company>/<source>/<doc_id>.raw       # untouched payload
        data/<company>/<source>/_manifest.json     # IngestManifest for the run

    ``doc_id`` is filesystem-sanitized. ``is_unchanged`` lets an ingester skip a
    document whose raw payload matches what is already on disk.
    """

    def __init__(self, root: Path = DEFAULT_DATA_ROOT) -> None:
        self.root = Path(root)

    def source_dir(self, company: str, source: str) -> Path:
        path = self.root / _safe_name(company) / _safe_name(source)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def is_unchanged(self, document: Document) -> bool:
        record_path = self.source_dir(document.company, document.source) / f"{_safe_name(document.doc_id)}.json"
        if not record_path.exists():
            return False
        try:
            existing = json.loads(record_path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return existing.get("hash") == document.hash

    def write_document(self, document: Document) -> None:
        directory = self.source_dir(document.company, document.source)
        stem = _safe_name(document.doc_id)
        (directory / f"{stem}.json").write_text(json.dumps(document.to_record(), indent=2))
        if document.text:
            (directory / f"{stem}.txt").write_text(document.text)
        if document.raw:
            (directory / f"{stem}.raw").write_text(document.raw)

    def load_documents(self, company: str, source: Optional[str] = None) -> List[Document]:
        """Load stored documents for a company (optionally one source) back into memory.

        Snapshot semantics: when a source has a ``_manifest.json``, only the documents
        that run recorded are loaded. Files left on disk by an earlier, larger run
        (orphans) are ignored, so the corpus you read always corresponds to the latest
        ingestion of that source rather than the union of every run. When no manifest is
        present (e.g. documents written directly in a test), every document file is read.

        Reads the metadata record plus its ``.txt`` payload for each document. The bulky
        ``.raw`` payload is left on disk unless a caller needs it. This is the entry point
        downstream stages (RAG, analysis) use to read the corpus.
        """
        company_dir = self.root / _safe_name(company)
        if not company_dir.exists():
            return []
        if source is not None:
            source_dirs = [company_dir / _safe_name(source)]
        else:
            # figures/ holds figure artifacts (manifests, annotated images), not ingested
            # text documents; never scan it as a document source.
            source_dirs = [
                p for p in company_dir.iterdir() if p.is_dir() and p.name != "figures"
            ]

        documents: List[Document] = []
        for directory in source_dirs:
            if not directory.exists():
                continue
            allowed = self._manifest_doc_stems(directory)
            for json_path in sorted(directory.glob("*.json")):
                if json_path.name == "_manifest.json":
                    continue
                if allowed is not None and json_path.stem not in allowed:
                    continue  # orphan from a prior run; not part of the latest snapshot
                try:
                    record = json.loads(json_path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                text_path = json_path.with_suffix(".txt")
                text = text_path.read_text() if text_path.exists() else ""
                documents.append(Document.from_record(record, text=text))
        return documents

    @staticmethod
    def _manifest_doc_stems(directory: Path) -> Optional[set]:
        """Sanitized doc-id stems recorded in a source's manifest, or None if absent."""
        manifest_path = directory / "_manifest.json"
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return {_safe_name(str(d.get("doc_id", ""))) for d in manifest.get("documents", [])}

    def write_manifest(self, manifest: IngestManifest) -> Path:
        directory = self.source_dir(manifest.company, manifest.source)
        path = directory / "_manifest.json"
        path.write_text(json.dumps(manifest.to_dict(), indent=2))
        return path


def _safe_name(value: str) -> str:
    """Sanitize a value for safe use as a single path segment."""
    keep = [c if c.isalnum() or c in ("-", "_", ".") else "_" for c in value.strip()]
    return "".join(keep) or "unknown"


class BaseIngester(abc.ABC):
    """Base class for all source ingesters.

    Subclasses implement :meth:`fetch`, which yields or returns
    :class:`Document` objects for a company. :meth:`run` orchestrates the run:
    it builds a manifest, writes each document via :class:`Storage` (skipping
    unchanged ones unless ``force``), records errors, and returns the manifest.

    Subclasses set :attr:`source` to their source key (e.g. ``"edgar"``).
    """

    #: Source key, e.g. "edgar" or "clinicaltrials". Must be set by subclasses.
    source: str = ""

    def __init__(
        self,
        http: Optional[HttpClient] = None,
        storage: Optional[Storage] = None,
    ) -> None:
        if not self.source:
            raise ValueError(f"{type(self).__name__} must define a non-empty 'source'")
        self.http = http or HttpClient()
        self.storage = storage or Storage()

    @abc.abstractmethod
    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Fetch documents for ``company``. Implemented by each source."""
        raise NotImplementedError

    def run(self, company: str, force: bool = False, **options: Any) -> IngestManifest:
        """Fetch, persist, and summarize an ingestion run for ``company``."""
        logger.info("Ingesting %s for %s", self.source, company)
        manifest = IngestManifest(company=company, source=self.source, started_at=utc_now_iso())
        try:
            documents = self.fetch(company, **options)
        except Exception as exc:  # noqa: BLE001 - surface fetch failure in manifest
            logger.exception("fetch failed for %s/%s", self.source, company)
            manifest.errors.append(f"fetch failed: {exc}")
            manifest.finished_at = utc_now_iso()
            self.storage.write_manifest(manifest)
            return manifest

        for document in documents:
            try:
                if not force and self.storage.is_unchanged(document):
                    logger.debug("unchanged, skipping %s", document.doc_id)
                else:
                    self.storage.write_document(document)
                manifest.add(document)
            except Exception as exc:  # noqa: BLE001 - one bad doc should not abort the run
                logger.exception("failed to write %s", document.doc_id)
                manifest.errors.append(f"{document.doc_id}: {exc}")

        manifest.finished_at = utc_now_iso()
        self.storage.write_manifest(manifest)
        logger.info("Ingested %d documents for %s/%s (%d errors)",
                    manifest.document_count, self.source, company, len(manifest.errors))
        return manifest
