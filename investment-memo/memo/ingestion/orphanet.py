"""Orphanet / Orphadata rare-disease epidemiology (prevalence) ingester.

Market sizing needs a disease's prevalence. The CDC source only covers common
chronic-disease surveillance series, so for the *rare* indications these companies
actually target (generalized myasthenia gravis, CIDP, systemic mastocytosis, GIST,
developmental and epileptic encephalopathy) it is only a loose proxy. Orphanet is
the canonical, citable source for orphan-disease epidemiology, and Orphadata
exposes it as keyless JSON.

Flow:

1. Load the epidemiology ORPHAcode index once (``/rd-epidemiology/orphacodes``),
   which lists every rare disease that has epidemiological annotations, so a
   disorder name can be resolved to its stable ORPHAcode. A common disease with
   no orphan-epidemiology record (e.g. Graves' disease, hidradenitis suppurativa,
   ulcerative colitis) is simply absent from this index and is reported as
   not-found rather than forced.
2. For each resolved disorder, fetch its epidemiology record
   (``/rd-epidemiology/orphacodes/{orphacode}``), which nests a list of prevalence
   / incidence entries, each with a type (point prevalence, annual incidence,
   lifetime prevalence, ...), a prevalence class band ("1-9 / 100 000"), a mean
   value, a geographic area, a validation status, and a source (often a PMID).
3. Emit one :class:`Document` (``doc_type="prevalence"``) per prevalence entry,
   carrying the structured fields for exact citation plus a searchable sentence
   naming the disease and the words "prevalence"/"epidemiology" so BM25 finds it.

Only the standard library is used. All network access goes through ``self.http``
(the shared :class:`HttpClient`), which sets a descriptive User-Agent and
rate-limits. Real data only: a disorder with no Orphanet epidemiology record, or
no prevalence entries, yields no documents and is never fabricated.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .base import BaseIngester, Document, HttpClient, Storage

# Orphadata API (keyless). The index lists every ORPHAcode that carries
# epidemiology data; the per-code endpoint returns its prevalence entries.
API_BASE = "https://api.orphadata.com"
EPI_INDEX_URL = f"{API_BASE}/rd-epidemiology/orphacodes"
EPI_BY_CODE_URL = f"{API_BASE}/rd-epidemiology/orphacodes/{{orphacode}}"


@dataclass(frozen=True)
class Indication:
    """A disease we want prevalence for.

    ``name`` is matched case-insensitively against Orphanet preferred terms.
    ``orphacode`` pins the resolution when the preferred term does not match the
    colloquial name exactly (e.g. "developmental and epileptic encephalopathy",
    whose Orphanet term is "Early infantile developmental and epileptic
    encephalopathy"); leave it ``None`` to resolve by exact name.
    """

    name: str
    orphacode: Optional[int] = None


# Per-company target indications, derived from each company's clinicaltrials.gov
# conditions in the existing corpus. Rare diseases resolve to an ORPHAcode and get
# prevalence documents; common conditions (Graves' disease, thyroid eye disease,
# hidradenitis suppurativa, atopic dermatitis, ulcerative colitis, Crohn disease,
# essential tremor) are not orphan diseases, are absent from Orphanet's
# epidemiology dataset, and are reported as not-found rather than forced.
COMPANY_INDICATIONS: Dict[str, Sequence[Indication]] = {
    "ABVX": (
        Indication("Ulcerative colitis"),
        Indication("Crohn disease"),
    ),
    "KYMR": (
        Indication("Hidradenitis suppurativa"),
        Indication("Atopic dermatitis"),
    ),
    "PRAX": (
        # PRAX-562 targets SCN2A/SCN8A DEE; those gene-specific entities have no
        # Orphanet epidemiology record, so use the parent DEE category (ORPHA:1934).
        Indication("Developmental and epileptic encephalopathy", orphacode=1934),
        Indication("Essential tremor"),
    ),
    "IMVT": (
        Indication("Myasthenia gravis"),
        Indication("Chronic inflammatory demyelinating polyneuropathy"),
        Indication("Graves disease"),
        Indication("Thyroid eye disease"),
    ),
    "COGT": (
        Indication("Systemic mastocytosis"),
        Indication("Gastrointestinal stromal tumor"),
    ),
}

_PMID_RE = re.compile(r"(\d+)\[PMID\]")


class OrphanetIngester(BaseIngester):
    """Ingest Orphanet rare-disease prevalence for a company's indications.

    Usage::

        manifest = OrphanetIngester().run("IMVT")

    ``fetch`` / ``run`` options:

    - ``disorders``: iterable overriding the default per-company indications. Each
      item may be a plain disorder name (``"Systemic mastocytosis"``), an integer
      ORPHAcode (``2467``), or an :class:`Indication`.

    One :class:`Document` (``doc_type="prevalence"``) per prevalence/incidence
    entry Orphanet reports for each resolved disorder. Disorders with no Orphanet
    epidemiology record, or no prevalence entries, contribute nothing.
    """

    source = "orphanet"

    def __init__(
        self,
        http: Optional[HttpClient] = None,
        storage: Optional[Storage] = None,
    ) -> None:
        super().__init__(http=http, storage=storage)
        # lower(preferred term) -> orphacode; built once from the epi index.
        self._epi_index: Optional[Dict[str, int]] = None
        # Disorders that could not be resolved on the most recent fetch, for the
        # caller/manifest to report honestly (name -> reason).
        self.unresolved: Dict[str, str] = {}

    # ------------------------------------------------------------------ public

    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Fetch Orphanet prevalence documents for ``company`` (a ticker)."""
        ticker = company.strip().upper()
        indications = self._indications_for(ticker, options.get("disorders"))
        self.unresolved = {}

        documents: List[Document] = []
        for indication in indications:
            orphacode = self._resolve_orphacode(indication)
            if orphacode is None:
                self.unresolved[indication.name] = (
                    "no Orphanet epidemiology record (not an orphan disease "
                    "in Orphanet's dataset)"
                )
                continue
            results = self._fetch_epidemiology(orphacode)
            if not results:
                self.unresolved[indication.name] = (
                    f"ORPHAcode {orphacode} has no epidemiology record"
                )
                continue
            entries = results.get("Prevalence") or []
            if not entries:
                self.unresolved[indication.name] = (
                    f"ORPHAcode {orphacode} has no prevalence entries"
                )
                continue
            for entry in entries:
                doc = self._to_document(ticker, results, entry)
                if doc is not None:
                    documents.append(doc)
        return documents

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _indications_for(
        ticker: str, override: Optional[Any]
    ) -> List[Indication]:
        """Resolve the indication list: explicit override, else the company map."""
        if override is not None:
            resolved: List[Indication] = []
            for item in override:
                if isinstance(item, Indication):
                    resolved.append(item)
                elif isinstance(item, int):
                    resolved.append(Indication(f"ORPHA:{item}", orphacode=item))
                else:
                    resolved.append(Indication(str(item)))
            return resolved
        return list(COMPANY_INDICATIONS.get(ticker, ()))

    def _load_epi_index(self) -> Dict[str, int]:
        """Fetch and index the epidemiology ORPHAcode list as name -> code.

        Cached per instance so a multi-disorder run makes a single index request.
        """
        if self._epi_index is not None:
            return self._epi_index
        payload = self.http.get_json(EPI_INDEX_URL)
        results = ((payload or {}).get("data") or {}).get("results") or []
        index: Dict[str, int] = {}
        for row in results:
            term = str(row.get("Preferred term") or "").strip().lower()
            code = row.get("ORPHAcode")
            if term and isinstance(code, int):
                index[term] = code
        self._epi_index = index
        return index

    def _resolve_orphacode(self, indication: Indication) -> Optional[int]:
        """Resolve an indication to an ORPHAcode, or None if not in Orphanet.

        A pinned ``orphacode`` wins outright. Otherwise the disorder name must
        match an Orphanet preferred term *exactly* (case-insensitive): this avoids
        false positives from substring matches (e.g. "essential tremor" partially
        matching a rare tremor *syndrome*), so a common disease correctly resolves
        to nothing.
        """
        if indication.orphacode is not None:
            return indication.orphacode
        index = self._load_epi_index()
        return index.get(indication.name.strip().lower())

    def _fetch_epidemiology(self, orphacode: int) -> Optional[Dict[str, Any]]:
        """Fetch the epidemiology record (results dict) for one ORPHAcode."""
        payload = self.http.get_json(EPI_BY_CODE_URL.format(orphacode=orphacode))
        results = ((payload or {}).get("data") or {}).get("results")
        if isinstance(results, dict):
            return results
        return None

    # ------------------------------------------------------------ doc assembly

    def _to_document(
        self,
        ticker: str,
        results: Dict[str, Any],
        entry: Dict[str, Any],
    ) -> Optional[Document]:
        orphacode = results.get("ORPHAcode")
        term = str(results.get("Preferred term") or "").strip()
        if orphacode is None or not term:
            return None

        prev_type = str(entry.get("PrevalenceType") or "").strip()
        prev_class = str(entry.get("PrevalenceClass") or "").strip()
        qualification = str(entry.get("PrevalenceQualification") or "").strip()
        geo = str(entry.get("PrevalenceGeographic") or "").strip()
        val_moy = str(entry.get("ValMoy") or "").strip()
        validation = str(entry.get("PrevalenceValidationStatus") or "").strip()
        source = str(entry.get("Source") or "").strip()
        pmids = _PMID_RE.findall(source)
        orphanet_url = str(results.get("OrphanetURL") or "").strip()

        raw = json.dumps(
            {"ORPHAcode": orphacode, "Preferred term": term, **entry},
            sort_keys=True,
        )
        doc_id = self._doc_id(int(orphacode), prev_type, geo, prev_class, raw)

        metadata: Dict[str, Any] = {
            "orphacode": int(orphacode),
            "preferred_term": term,
            "disorder_group": results.get("DisorderGroup"),
            "typology": results.get("Typology"),
            "prevalence_type": prev_type,
            "prevalence_class": prev_class,
            "prevalence_qualification": qualification,
            "value_average": val_moy,
            "geographic_area": geo,
            "validation_status": validation,
            "source": source,
            "source_pmids": pmids,
            "orphanet_url": orphanet_url,
            "api_url": EPI_BY_CODE_URL.format(orphacode=orphacode),
            "annotation_date": results.get("Date"),
        }

        return Document(
            company=ticker,
            source=self.source,
            doc_type="prevalence",
            doc_id=doc_id,
            title=f"{term} - {prev_type or 'prevalence'} ({geo or 'unknown area'}) [ORPHA:{orphacode}]",
            url=orphanet_url or EPI_BY_CODE_URL.format(orphacode=orphacode),
            published=_date_part(results.get("Date")),
            metadata=metadata,
            text=self._summary(term, int(orphacode), entry, pmids),
            raw=raw,
        )

    @staticmethod
    def _doc_id(
        orphacode: int, prev_type: str, geo: str, prev_class: str, raw: str
    ) -> str:
        """Deterministic per-entry id: ORPHAcode + type + area + a content hash.

        Orphanet can report several entries of the same type for one area (e.g.
        different classes/sources), so a short hash of the full entry keeps ids
        unique and stable across re-runs without depending on list order.
        """
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        return f"orpha{orphacode}-{_slug(prev_type)}-{_slug(geo)}-{digest}"

    @staticmethod
    def _summary(
        term: str, orphacode: int, entry: Dict[str, Any], pmids: Sequence[str]
    ) -> str:
        """Searchable, citable sentence naming the disease, value/class, and source.

        Always contains the words "prevalence" and "epidemiology" so BM25 matches an
        epidemiology query, plus the disease name and the concrete value/class band.
        """
        prev_type = str(entry.get("PrevalenceType") or "prevalence").strip()
        prev_class = str(entry.get("PrevalenceClass") or "").strip()
        val_moy = str(entry.get("ValMoy") or "").strip()
        geo = str(entry.get("PrevalenceGeographic") or "").strip()
        validation = str(entry.get("PrevalenceValidationStatus") or "").strip()

        measure = f"{term} (ORPHA:{orphacode}): {prev_type}"
        if prev_class:
            measure += f" {prev_class}"
        try:
            if val_moy and float(val_moy) != 0.0:
                measure += f" (mean {val_moy} per relevant denominator)"
        except ValueError:
            pass
        if geo:
            measure += f" in {geo}"
        parts = [measure + "."]
        parts.append(
            f"Orphanet epidemiology / prevalence record for {term}"
            + (f", validation status {validation}" if validation else "")
            + "."
        )
        if pmids:
            parts.append("Source PMID: " + ", ".join(pmids) + ".")
        return " ".join(parts)


# ---------------------------------------------------------------- helpers


def _slug(value: str) -> str:
    """Lowercase, hyphenated slug of a short label for use in a doc_id."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "na"


def _date_part(value: Any) -> Optional[str]:
    """Take the ``YYYY-MM-DD`` date part of an Orphanet timestamp, if present."""
    if not value:
        return None
    return str(value).strip().split(" ", 1)[0] or None
