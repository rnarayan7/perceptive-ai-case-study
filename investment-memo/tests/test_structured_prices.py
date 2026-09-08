"""Tests for the structured price accessors (CMS spending + NADAC acquisition cost).

Deterministic and offline: synthetic ``cms``/``nadac`` Documents are written to a tmp
Storage, then read back through :class:`StructuredStore` as typed :class:`PriceRecord`s.
No live CMS/NADAC calls and no model calls. A string-numeric case proves the defensive
float casting that reloading a JSON record (which stringifies) would otherwise expose.
"""

from __future__ import annotations

from memo.ingestion.base import Document, Storage
from memo.rag.structured import PriceRecord, StructuredStore


def _cms_doc(doc_id, price, **overrides):
    metadata = {
        "brand_name": "Dupixent",
        "generic_name": "dupilumab",
        "manufacturer": "Overall",
        "year": "2024",
        "avg_spending_per_dosage_unit": price,
        "total_spending": 1_000_000_000.0,
    }
    metadata.update(overrides)
    return Document(
        company="TEST",
        source="cms",
        doc_type="spending",
        doc_id=doc_id,
        title="Medicare Part D spending: Dupixent (dupilumab), 2024",
        url="https://data.cms.gov/.../medicare-part-d-spending-by-drug?keyword=Dupixent",
        published="2024",
        metadata=metadata,
        text="Dupixent (dupilumab) - Medicare Part D spending, 2024.",
    )


def _nadac_doc(doc_id, price, **overrides):
    metadata = {
        "ndc": "00024589001",
        "ndc_description": "DUPIXENT 300 MG/2ML SYRINGE",
        "nadac_per_unit": price,
        "pricing_unit": "ML",
        "effective_date": "2026-08-01",
    }
    metadata.update(overrides)
    return Document(
        company="TEST",
        source="nadac",
        doc_type="nadac_price",
        doc_id=doc_id,
        title="NADAC price: DUPIXENT 300 MG/2ML SYRINGE",
        url="https://data.medicaid.gov/dataset/fbb83258-11c7-47f5-8b18-5f8e79f7e704",
        published="2026-08-01",
        metadata=metadata,
        text="Drug: DUPIXENT 300 MG/2ML SYRINGE\nNADAC per unit: $0.01419",
    )


def test_prices_returns_typed_records_from_cms(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(_cms_doc("Dupixent_2024", 34.56))

    records = StructuredStore("TEST", storage=storage).prices()

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, PriceRecord)
    assert record.source == "cms"
    assert record.doc_type == "spending"
    assert record.drug == "Dupixent"
    assert record.generic == "dupilumab"
    assert record.unit == "dosage unit"
    assert record.period == "2024"
    # price is a real float, and the citation url is populated.
    assert isinstance(record.price_per_unit, float)
    assert record.price_per_unit == 34.56
    assert record.url.startswith("https://data.cms.gov/")
    assert record.doc_id == "Dupixent_2024"


def test_acquisition_costs_returns_typed_records_from_nadac(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(_nadac_doc("00024589001-2026-08-01", 0.01419))

    records = StructuredStore("TEST", storage=storage).acquisition_costs()

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, PriceRecord)
    assert record.source == "nadac"
    assert record.doc_type == "nadac_price"
    assert record.drug == "DUPIXENT 300 MG/2ML SYRINGE"
    assert record.unit == "ML"
    assert record.period == "2026-08-01"
    assert isinstance(record.price_per_unit, float)
    assert record.price_per_unit == 0.01419
    assert record.url.startswith("https://data.medicaid.gov/")


def test_prices_casts_string_numeric_to_float(tmp_path):
    # A record reloaded from JSON can carry the price as a string ("$12.50", "1,234");
    # the accessor must coerce it to a float rather than pass it through.
    storage = Storage(root=tmp_path)
    storage.write_document(_cms_doc("StringPrice_2024", "12.50"))

    records = StructuredStore("TEST", storage=storage).prices()

    assert len(records) == 1
    assert isinstance(records[0].price_per_unit, float)
    assert records[0].price_per_unit == 12.50


def test_acquisition_costs_casts_string_numeric_to_float(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(_nadac_doc("StringNadac-2026-08-01", "0.02531"))

    records = StructuredStore("TEST", storage=storage).acquisition_costs()

    assert len(records) == 1
    assert isinstance(records[0].price_per_unit, float)
    assert records[0].price_per_unit == 0.02531


def test_unparseable_price_is_skipped(tmp_path):
    # Suppressed / empty CMS cells must not become records with a bogus price.
    storage = Storage(root=tmp_path)
    storage.write_document(_cms_doc("Suppressed_2024", "*"))
    storage.write_document(_cms_doc("Good_2024", 20.0))

    records = StructuredStore("TEST", storage=storage).prices()

    assert [r.doc_id for r in records] == ["Good_2024"]


def test_accessors_ignore_other_sources_and_doc_types(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(_cms_doc("Dupixent_2024", 34.56))
    storage.write_document(_nadac_doc("00024589001-2026-08-01", 0.01419))
    # An unrelated source must not leak into either accessor.
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-K", doc_id="F1",
        title="10-K", url="https://example.com/F1", text="filing",
    ))

    store = StructuredStore("TEST", storage=storage)
    assert [r.source for r in store.prices()] == ["cms"]
    assert [r.source for r in store.acquisition_costs()] == ["nadac"]
