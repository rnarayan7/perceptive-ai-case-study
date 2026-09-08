"""PubChem ingestion via PUG-REST.

Small-molecule chemistry backs the mechanism-of-action story that filings and trial
registries only gesture at: the structure, molecular formula, and identifiers of a
program's assets or its named comparators. This queries PubChem by compound name,
resolves each to a PubChem CID, and pulls key properties plus synonyms.

Keyless JSON REST, verified 2026-09-07 (see docs/source-ingestion-feasibility.md, row
"PubChem PUG-REST"). PubChem tolerates ~5 requests/second; the shared HttpClient's
throttle keeps us well under that.

Biologics, antibodies, and novel degraders usually have no CID (the name does not
resolve). Those are skipped, not fatal: an all-biologics company simply yields no
compounds, which is the correct result rather than an error.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib.parse import quote

from memo.ingestion.base import BaseIngester, Document

# Asset codes like "KT-474" or "SAR444656" appearing in trial intervention names.
# Same shape the PubMed ingester keys on. Small molecules with a public CID may
# resolve from such a code; biologics simply will not, and are skipped.
_DRUG_CODE = re.compile(r"\b[A-Z]{2,6}-?\d{3,}[A-Z0-9]*\b")

_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_COMPOUND_PAGE = "https://pubchem.ncbi.nlm.nih.gov/compound"

# Properties requested per compound. "SMILES" is PubChem's current canonical SMILES
# key (the older "CanonicalSMILES" now echoes back as "ConnectivitySMILES"); we ask
# for a couple of aliases and take whichever the payload actually carries.
_PROPERTIES = "MolecularFormula,MolecularWeight,SMILES,IUPACName"
_SMILES_KEYS = ("SMILES", "CanonicalSMILES", "ConnectivitySMILES")

_MAX_SYNONYMS = 15  # enough to be useful in text; full set is preserved in raw
_MAX_NAMES = 25  # cap derived names so one company cannot fan out unbounded


class PubChemIngester(BaseIngester):
    """Ingest small-molecule chemistry from PubChem PUG-REST.

    One :class:`Document` per resolved compound (``doc_type`` ``"compound"``,
    ``doc_id`` the CID). Names that do not resolve to a CID are skipped.
    """

    source = "pubchem"

    def fetch(self, company: str, **options: Any) -> List[Document]:
        """Fetch compound records for ``company``.

        Options:
          - ``name``: a single compound name to look up (override; used alone).
          - ``compounds``: a list of compound names to look up.
          - neither: names are derived from the company's already-ingested
            clinicaltrials intervention codes.

        Returns one Document per name that resolves to a PubChem CID. Names with no
        CID are skipped, so an empty list is a valid, expected result.
        """
        names = self._resolve_names(company, options)
        if not names:
            return []

        documents: List[Document] = []
        seen_cids: set = set()
        for name in names:
            document = self._fetch_compound(company, name)
            if document is None:
                continue  # unresolved name; already noted in the log
            if document.doc_id in seen_cids:
                continue  # two names mapped to the same CID
            seen_cids.add(document.doc_id)
            documents.append(document)
        return documents

    # ---------------------------------------------------------------- name selection

    def _resolve_names(self, company: str, options: Dict[str, Any]) -> List[str]:
        """Decide which compound names to look up, in priority order.

        A ``name`` override wins outright. Otherwise combine any explicit
        ``compounds`` list with drug codes derived from ingested trials.
        """
        override = options.get("name")
        if override:
            return [str(override)]

        names: List[str] = []
        seen = set()
        for candidate in list(options.get("compounds") or []) + self._drug_codes(company):
            value = str(candidate).strip()
            key = value.lower()
            if value and key not in seen:
                seen.add(key)
                names.append(value)
        return names[:_MAX_NAMES]

    def _drug_codes(self, company: str) -> List[str]:
        """Asset codes pulled from already-ingested clinicaltrials interventions.

        Read directly through Storage (same approach as the PubMed ingester) so this
        stays company-agnostic and carries no dependency on the RAG layer.
        """
        codes: List[str] = []
        seen = set()
        for doc in self.storage.load_documents(company, source="clinicaltrials"):
            for item in (doc.metadata or {}).get("interventions", []):
                name = item.get("name", "") if isinstance(item, dict) else str(item)
                for match in _DRUG_CODE.findall(name):
                    if match not in seen:
                        seen.add(match)
                        codes.append(match)
        return codes

    # ---------------------------------------------------------------- per-compound

    def _fetch_compound(self, company: str, name: str) -> Optional[Document]:
        """Resolve one name to a CID and build its Document, or None if unresolved."""
        cid = self._resolve_cid(name)
        if cid is None:
            return None

        properties = self._fetch_properties(cid) or {}
        synonyms = self._fetch_synonyms(cid)

        iupac = str(properties.get("IUPACName") or "").strip()
        title = iupac or name
        raw = {"query_name": name, "cid": cid, "properties": properties, "synonyms": synonyms}

        return Document(
            company=company,
            source=self.source,
            doc_type="compound",
            doc_id=str(cid),
            title=title,
            url=f"{_COMPOUND_PAGE}/{cid}",
            metadata={
                "cid": cid,
                "query_name": name,
                "molecular_formula": properties.get("MolecularFormula"),
                "molecular_weight": properties.get("MolecularWeight"),
                "smiles": _first_smiles(properties),
                "iupac_name": iupac or None,
                "synonyms": synonyms[:_MAX_SYNONYMS],
            },
            text=_summary_text(name, cid, properties, synonyms),
            raw=json.dumps(raw, indent=2),
        )

    def _resolve_cid(self, name: str) -> Optional[int]:
        """name -> CID. Returns the first (best) CID, or None when the name is unknown."""
        url = f"{_BASE}/compound/name/{quote(name, safe='')}/cids/JSON"
        payload = self._get_json_or_none(url)
        if not payload:
            return None
        cids = _safe_get(payload, "IdentifierList", "CID") or []
        return int(cids[0]) if cids else None

    def _fetch_properties(self, cid: int) -> Dict[str, Any]:
        """Key properties for a CID: formula, weight, SMILES, IUPAC name."""
        url = f"{_BASE}/compound/cid/{cid}/property/{_PROPERTIES}/JSON"
        payload = self._get_json_or_none(url)
        if not payload:
            return {}
        rows = _safe_get(payload, "PropertyTable", "Properties") or []
        return rows[0] if rows else {}

    def _fetch_synonyms(self, cid: int) -> List[str]:
        """Registered synonyms for a CID (trade names, registry ids, aliases)."""
        url = f"{_BASE}/compound/cid/{cid}/synonyms/JSON"
        payload = self._get_json_or_none(url)
        if not payload:
            return []
        rows = _safe_get(payload, "InformationList", "Information") or []
        if not rows:
            return []
        return [str(s) for s in (rows[0].get("Synonym") or [])]

    def _get_json_or_none(self, url: str) -> Optional[Any]:
        """GET JSON, mapping PubChem "not found" and transient failures to None.

        A missing name returns HTTP 404 (which the shared client raises as an
        HTTPError); that must not abort the run, so it is swallowed here. Other
        request failures are likewise treated as "no data for this compound" so one
        bad lookup never sinks the others.
        """
        try:
            return self.http.get_json(url)
        except urlerror.HTTPError as exc:
            if exc.code == 404:
                return None  # unknown name / no such record: expected, skip quietly
            raise
        except (urlerror.URLError, RuntimeError, TimeoutError, ValueError):
            return None


# ------------------------------------------------------------------------ helpers


def _safe_get(payload: Any, *keys: str) -> Any:
    """Walk nested dict keys, returning None if any level is missing or not a dict."""
    node = payload
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _first_smiles(properties: Dict[str, Any]) -> Optional[str]:
    """Pick whichever SMILES field PubChem returned for this compound."""
    for key in _SMILES_KEYS:
        value = properties.get(key)
        if value:
            return str(value)
    return None


def _summary_text(name: str, cid: int, properties: Dict[str, Any], synonyms: List[str]) -> str:
    """Readable plain-text rendering of a compound for indexing."""
    lines = [f"Compound: {name}", f"PubChem CID: {cid}"]
    iupac = properties.get("IUPACName")
    if iupac:
        lines.append(f"IUPAC name: {iupac}")
    formula = properties.get("MolecularFormula")
    if formula:
        lines.append(f"Molecular formula: {formula}")
    weight = properties.get("MolecularWeight")
    if weight:
        lines.append(f"Molecular weight: {weight} g/mol")
    smiles = _first_smiles(properties)
    if smiles:
        lines.append(f"SMILES: {smiles}")
    if synonyms:
        shown = ", ".join(synonyms[:_MAX_SYNONYMS])
        lines.append(f"Synonyms: {shown}")
    return "\n".join(lines)
