"""openFDA ingestion: drug labels, FAERS adverse events, and Drugs@FDA.

openFDA is keyless JSON with Elasticsearch-style query syntax. Three endpoints,
one client, one helper each:

- drug label      https://api.fda.gov/drug/label.json      (indications, mechanism)
- FAERS events    https://api.fda.gov/drug/event.json      (adverse-reaction signals)
- Drugs@FDA       https://api.fda.gov/drug/drugsfda.json    (approvals, review timeline)

Endpoints verified live 2026-09-07 (see docs/source-ingestion-feasibility.md).

A constraint shapes the whole module: Kymera's assets (KT-474, KT-621, KT-333,
KT-253) are INVESTIGATIONAL. They have no label, no FAERS record, and no Drugs@FDA
application, so openFDA returns nothing for them. This source is therefore a
COMPARATOR / class-precedent tool: the caller passes the approved drugs or the
indication to look up (e.g. dupilumab for the atopic-dermatitis anchor). Returning
zero documents for a company whose own drugs are not approved is CORRECT, not an
error, and this ingester never raises on empty.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib.parse import urlencode

from memo.ingestion.base import BaseIngester, Document, logger

_LABEL = "https://api.fda.gov/drug/label.json"
_EVENT = "https://api.fda.gov/drug/event.json"
_DRUGSFDA = "https://api.fda.gov/drug/drugsfda.json"

# openFDA caps skip+limit at 1000 and limit at 100 per request; keep well under.
_MAX_LIMIT = 100


class OpenFdaIngester(BaseIngester):
    """Ingest comparator drug data from openFDA (labels, FAERS, Drugs@FDA).

    Because the memo's subject companies are typically pre-approval, ``fetch``
    looks up *comparators*, not the company's own assets. Supply them explicitly:

    - ``terms``: list of drug / ingredient names (generic or brand), e.g.
      ``["dupilumab", "Cosentyx"]``. Each is looked up across all requested
      endpoints.
    - ``indication``: a condition string, e.g. ``"atopic dermatitis"``, matched
      against label indications to surface the whole approved class.

    Other options: ``limit`` (records per endpoint per term, default 10),
    ``endpoints`` (subset of ``{"label", "adverse_event", "drugsfda"}``, default
    all three). With neither ``terms`` nor ``indication`` given, ``fetch`` returns
    an empty list and logs a note, which is the expected result for an
    all-investigational pipeline such as KYMR.
    """

    source = "openfda"

    def fetch(self, company: str, **options: Any) -> List[Document]:
        terms = self._normalize_terms(options.get("terms"))
        indication = (options.get("indication") or "").strip()
        limit = self._clamp_limit(options.get("limit", 10))
        endpoints = self._normalize_endpoints(options.get("endpoints"))

        if not terms and not indication:
            logger.info(
                "openfda: no comparator 'terms' or 'indication' for %s; returning "
                "empty (its assets are likely investigational and absent from openFDA)",
                company,
            )
            return []

        documents: List[Document] = []
        seen: set = set()

        for term in terms:
            if "label" in endpoints:
                documents.extend(self._collect(seen, self._fetch_labels(company, term, limit)))
            if "adverse_event" in endpoints:
                documents.extend(self._collect(seen, self._fetch_adverse_events(company, term, limit)))
            if "drugsfda" in endpoints:
                documents.extend(self._collect(seen, self._fetch_drugsfda(company, term, limit)))

        if indication and "label" in endpoints:
            search = f'indications_and_usage:"{indication}"'
            documents.extend(
                self._collect(seen, self._labels_from_search(company, search, limit))
            )

        return documents

    # ---------------------------------------------------------------- endpoints

    def _fetch_labels(self, company: str, term: str, limit: int) -> List[Document]:
        """Drug label records for one comparator (indications, mechanism, warnings)."""
        search = (
            f'(openfda.generic_name:"{term}") '
            f'OR (openfda.brand_name:"{term}") '
            f'OR (openfda.substance_name:"{term}")'
        )
        return self._labels_from_search(company, search, limit)

    def _labels_from_search(self, company: str, search: str, limit: int) -> List[Document]:
        documents = []
        for record in self._query(_LABEL, search, limit):
            doc = self._label_document(company, record)
            if doc is not None:
                documents.append(doc)
        return documents

    def _fetch_adverse_events(self, company: str, term: str, limit: int) -> List[Document]:
        """FAERS adverse-event reports mentioning one comparator drug."""
        search = (
            f'(patient.drug.openfda.generic_name:"{term}") '
            f'OR (patient.drug.openfda.brand_name:"{term}") '
            f'OR (patient.drug.medicinalproduct:"{term}")'
        )
        documents = []
        for record in self._query(_EVENT, search, limit):
            doc = self._event_document(company, record, term)
            if doc is not None:
                documents.append(doc)
        return documents

    def _fetch_drugsfda(self, company: str, term: str, limit: int) -> List[Document]:
        """Drugs@FDA application records (type, approval dates, marketing status)."""
        search = (
            f'(openfda.generic_name:"{term}") '
            f'OR (products.brand_name:"{term}") '
            f'OR (openfda.substance_name:"{term}")'
        )
        documents = []
        for record in self._query(_DRUGSFDA, search, limit):
            doc = self._drugsfda_document(company, record)
            if doc is not None:
                documents.append(doc)
        return documents

    # ---------------------------------------------------------------- HTTP

    def _query(self, base: str, search: str, limit: int) -> List[Dict[str, Any]]:
        """GET a `results` list, treating openFDA's 404 (no matches) as empty.

        openFDA answers a query with zero hits with HTTP 404 and a
        ``{"error": {"code": "NOT_FOUND"}}`` body. For a comparator lookup that
        simply means the drug/indication is not in that dataset, which is a normal
        outcome here, not a failure. Every other error propagates so ``run`` can
        record it.
        """
        url = f"{base}?{urlencode({'search': search, 'limit': limit})}"
        try:
            payload = self.http.get_json(url)
        except urlerror.HTTPError as exc:
            if exc.code == 404:
                return []
            raise
        if not isinstance(payload, dict):
            return []
        results = payload.get("results")
        return results if isinstance(results, list) else []

    # ---------------------------------------------------------------- documents

    def _label_document(self, company: str, record: Dict[str, Any]) -> Optional[Document]:
        openfda = _as_dict(record.get("openfda"))
        set_id = record.get("set_id") or record.get("id")
        if not set_id:
            return None

        brand = _first(openfda.get("brand_name"))
        generic = _first(openfda.get("generic_name"))
        name = brand or generic or "Drug label"
        title = f"Label: {name}" + (f" ({generic})" if generic and generic != name else "")

        moa_class = _first(openfda.get("pharm_class_moa"))
        epc_class = _first(openfda.get("pharm_class_epc"))
        manufacturer = _first(openfda.get("manufacturer_name"))

        parts = [f"Drug: {name}"]
        if generic and generic != brand:
            parts.append(f"Generic name: {generic}")
        if manufacturer:
            parts.append(f"Manufacturer: {manufacturer}")
        if epc_class:
            parts.append(f"Established pharmacologic class: {epc_class}")
        if moa_class:
            parts.append(f"Mechanism class: {moa_class}")
        for label, key in (
            ("Indications", "indications_and_usage"),
            ("Mechanism of action", "mechanism_of_action"),
            ("Boxed warning", "boxed_warning"),
            ("Warnings and precautions", "warnings_and_cautions"),
            ("Warnings", "warnings"),
            ("Contraindications", "contraindications"),
        ):
            value = _join(record.get(key))
            if value:
                parts.append(f"{label}: {value}")
        text = "\n\n".join(parts)

        return Document(
            company=company,
            source=self.source,
            doc_type="label",
            doc_id=str(set_id),
            title=title,
            url=f"https://labels.fda.gov/getSPLDetail.cfm?setid={set_id}",
            published=_fmt_date(record.get("effective_time")),
            metadata={
                "brand_name": brand,
                "generic_name": generic,
                "manufacturer": manufacturer,  # shared vocabulary across drug sources
                "manufacturer_name": manufacturer,  # source-specific alias
                "pharm_class_epc": epc_class,
                "pharm_class_moa": moa_class,
                "application_number": _first(openfda.get("application_number")),
                "route": _first(openfda.get("route")),
            },
            text=text,
            raw=json.dumps(record),
        )

    def _event_document(
        self, company: str, record: Dict[str, Any], term: str
    ) -> Optional[Document]:
        report_id = record.get("safetyreportid")
        if not report_id:
            return None

        patient = _as_dict(record.get("patient"))
        reactions = [
            r.get("reactionmeddrapt")
            for r in _as_list(patient.get("reaction"))
            if isinstance(r, dict) and r.get("reactionmeddrapt")
        ]
        drugs = [
            d.get("medicinalproduct")
            for d in _as_list(patient.get("drug"))
            if isinstance(d, dict) and d.get("medicinalproduct")
        ]
        serious = record.get("serious") == "1"

        parts = [f"FAERS adverse-event report {report_id} (comparator: {term})"]
        parts.append("Serious: yes" if serious else "Serious: no")
        if reactions:
            parts.append("Reactions: " + ", ".join(reactions))
        if drugs:
            parts.append("Reported drugs: " + ", ".join(drugs))
        text = "\n".join(parts)

        title = f"FAERS report {report_id}: " + (
            ", ".join(reactions[:3]) if reactions else "adverse event"
        )

        return Document(
            company=company,
            source=self.source,
            doc_type="adverse_event",
            doc_id=str(report_id),
            title=title,
            url="https://api.fda.gov/drug/event.json?search=safetyreportid:" + str(report_id),
            published=_fmt_date(record.get("receiptdate")),
            metadata={
                "term": term,
                "serious": serious,
                "reactions": reactions,
                "drugs": drugs,
                "occurcountry": record.get("occurcountry"),
            },
            text=text,
            raw=json.dumps(record),
        )

    def _drugsfda_document(self, company: str, record: Dict[str, Any]) -> Optional[Document]:
        app_no = record.get("application_number")
        if not app_no:
            return None

        sponsor = record.get("sponsor_name")
        products = _as_list(record.get("products"))
        submissions = _as_list(record.get("submissions"))

        brands = sorted({p.get("brand_name") for p in products if isinstance(p, dict) and p.get("brand_name")})
        ingredients = sorted({
            ing.get("name")
            for p in products if isinstance(p, dict)
            for ing in _as_list(p.get("active_ingredients"))
            if isinstance(ing, dict) and ing.get("name")
        })
        marketing = sorted({p.get("marketing_status") for p in products if isinstance(p, dict) and p.get("marketing_status")})

        # Original approval: an ORIG submission with status AP.
        approval_date = None
        for sub in submissions:
            if not isinstance(sub, dict):
                continue
            if sub.get("submission_type") == "ORIG" and sub.get("submission_status") == "AP":
                approval_date = _fmt_date(sub.get("submission_status_date"))
                break
        priorities = sorted({
            s.get("review_priority") for s in submissions
            if isinstance(s, dict) and s.get("review_priority")
        })

        name = brands[0] if brands else (ingredients[0] if ingredients else app_no)
        parts = [f"Drugs@FDA application {app_no}: {name}"]
        if sponsor:
            parts.append(f"Sponsor: {sponsor}")
        if ingredients:
            parts.append("Active ingredient(s): " + ", ".join(ingredients))
        if brands:
            parts.append("Brand name(s): " + ", ".join(brands))
        parts.append(f"Application type: {_app_type(app_no)}")
        if approval_date:
            parts.append(f"Original approval date: {approval_date}")
        if priorities:
            parts.append("Review priority: " + ", ".join(priorities))
        if marketing:
            parts.append("Marketing status: " + ", ".join(marketing))
        parts.append(f"Submissions on record: {len(submissions)}")
        text = "\n".join(parts)

        return Document(
            company=company,
            source=self.source,
            doc_type="drugsfda",
            doc_id=str(app_no),
            title=f"Drugs@FDA {app_no}: {name}",
            url="https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo="
            + str(app_no).replace("BLA", "").replace("NDA", "").replace("ANDA", ""),
            published=approval_date,
            metadata={
                "manufacturer": sponsor,  # shared vocabulary across drug sources
                "brand_name": brands[0] if brands else None,  # shared vocabulary
                "sponsor_name": sponsor,
                "application_type": _app_type(app_no),
                "brand_names": brands,
                "active_ingredients": ingredients,
                "marketing_status": marketing,
                "approval_date": approval_date,
                "review_priority": priorities,
                "submission_count": len(submissions),
            },
            text=text,
            raw=json.dumps(record),
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _collect(seen: set, documents: List[Document]) -> List[Document]:
        """De-duplicate documents by (doc_type, doc_id) across terms/endpoints."""
        fresh = []
        for doc in documents:
            key = (doc.doc_type, doc.doc_id)
            if key in seen:
                continue
            seen.add(key)
            fresh.append(doc)
        return fresh

    @staticmethod
    def _normalize_terms(raw: Any) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            raw = [raw]
        terms = []
        for item in raw:
            value = str(item).strip()
            if value:
                terms.append(value)
        return terms

    @staticmethod
    def _normalize_endpoints(raw: Any) -> set:
        allowed = {"label", "adverse_event", "drugsfda"}
        if raw is None:
            return allowed
        if isinstance(raw, str):
            raw = [raw]
        chosen = {str(x).strip() for x in raw} & allowed
        return chosen or allowed

    @staticmethod
    def _clamp_limit(value: Any) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            limit = 10
        return max(1, min(limit, _MAX_LIMIT))


# ---------------------------------------------------------------- module helpers


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _first(value: Any) -> Optional[str]:
    """First element of an openFDA array field (they are almost always lists)."""
    if isinstance(value, list):
        return str(value[0]) if value else None
    if value in (None, ""):
        return None
    return str(value)


def _join(value: Any, sep: str = " ") -> str:
    """Flatten an openFDA text field (usually a list of paragraphs) into a string."""
    if isinstance(value, list):
        return sep.join(str(v).strip() for v in value if str(v).strip())
    if value in (None, ""):
        return ""
    return str(value).strip()


def _fmt_date(value: Any) -> Optional[str]:
    """Turn openFDA's YYYYMMDD strings into ISO dates; pass through anything else."""
    text = _first(value)
    if text and len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _app_type(app_no: str) -> str:
    app_no = str(app_no).upper()
    if app_no.startswith("BLA"):
        return "BLA (biologic)"
    if app_no.startswith("ANDA"):
        return "ANDA (generic)"
    if app_no.startswith("NDA"):
        return "NDA"
    return "NDA/BLA"
