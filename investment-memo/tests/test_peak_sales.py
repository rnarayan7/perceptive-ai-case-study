"""Tests for the peak-sales module.

The module's contract is that the model only picks and cites parameters while Python
does the arithmetic, so the deterministic tests carry the weight here: the pure
calculator is checked against hand-computed numbers, and the parameter->claim assembly
is exercised with a canned :class:`ModelResponse`. A single network-marked test runs the
full path against a real Claude model and self-skips without credentials (no mocks).
"""

from __future__ import annotations

import os

import pytest

from memo.analysis.base import AnalysisContext
from memo.analysis.model import ModelResponse
from memo.analysis.peak_sales import PeakSalesModule, estimate_peak_sales
from memo.ingestion.base import Document, Storage
from memo.rag import StructuredStore, build_retriever


# --------------------------------------------------------------------- fixtures


def _ctx(tmp_path):
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="pubmed", doc_type="article", doc_id="P1",
        title="Epidemiology of disease X", url="https://example.com/P1",
        text="Disease X affects an estimated 100,000 patients in the United States; "
             "roughly half are eligible for second-line therapy.",
    ))
    storage.write_document(Document(
        company="TEST", source="edgar", doc_type="10-K", doc_id="F1",
        title="10-K", url="https://example.com/F1",
        text="We estimate a substantial market opportunity across the addressable "
             "eligible patient population for our lead program.",
    ))
    return AnalysisContext(
        company="TEST",
        retriever=build_retriever("TEST", storage=storage),
        structured=StructuredStore("TEST", storage=storage),
        model=None,  # not used by the deterministic paths under test
    )


# ---------------------------------------------------- estimate_peak_sales (pure)


def test_estimate_peak_sales_exact_arithmetic():
    params = {
        "epidemiology_population": 100_000,
        "addressable_fraction": 0.5,
        "peak_penetration": 0.3,
        "annual_net_price": 100_000,
        "probability_of_success": 0.4,
    }
    out = estimate_peak_sales(params)

    assert out["treatable_population"] == 50_000
    assert out["patients_on_drug"] == 15_000
    assert out["gross_revenue"] == 1_500_000_000
    assert out["risk_adjusted"] == 600_000_000
    assert out["sensitivity"]["base"] == 600_000_000


def test_estimate_peak_sales_sensitivity_brackets_base():
    params = {
        "epidemiology_population": 100_000,
        "addressable_fraction": 0.5,
        "peak_penetration": 0.3,
        "annual_net_price": 100_000,
        "probability_of_success": 0.4,
    }
    out = estimate_peak_sales(params, sensitivity_margin=0.2)
    sens = out["sensitivity"]

    assert sens["low"] < sens["base"] < sens["high"]
    # +/-20% on both penetration and price: 0.8^2 and 1.2^2 of base.
    assert sens["low"] == pytest.approx(600_000_000 * 0.64)
    assert sens["high"] == pytest.approx(600_000_000 * 1.44)
    assert out["range_str"] == "$0.4-0.9B"


def test_estimate_peak_sales_range_string_format():
    params = {
        "epidemiology_population": 200_000,
        "addressable_fraction": 1.0,
        "peak_penetration": 0.5,
        "annual_net_price": 50_000,
        "probability_of_success": 1.0,
    }
    # base = 200000 * 0.5 * 50000 = 5e9
    out = estimate_peak_sales(params, sensitivity_margin=0.2)
    assert out["risk_adjusted"] == 5_000_000_000
    assert out["range_str"] == "$3.2-7.2B"


# ------------------------------------------------ parameter -> Claim assembly


def _canned_response(good_label, missing_price=False, unknown_label="E999"):
    """A ModelResponse mimicking the model citing parameters, some assumed."""
    params = [
        {"name": "epidemiology_population", "value": 100_000, "unit": "patients",
         "evidence_ids": [good_label], "rationale": "stated prevalence",
         "confidence": 0.7, "assumed": False},
        {"name": "addressable_fraction", "value": 0.5, "unit": "fraction",
         "evidence_ids": [good_label], "rationale": "half eligible",
         "confidence": 0.6, "assumed": False},
        {"name": "peak_penetration", "value": 0.3, "unit": "fraction",
         "evidence_ids": [], "rationale": "analyst assumption",
         "confidence": 0.2, "assumed": True},
        {"name": "annual_net_price", "value": 100_000, "unit": "USD/patient/year",
         "evidence_ids": [unknown_label], "rationale": "cited bad id",
         "confidence": 0.2, "assumed": False},
        {"name": "probability_of_success", "value": 0.4, "unit": "probability",
         "evidence_ids": [good_label], "rationale": "phase 2 asset",
         "confidence": 0.4, "assumed": False},
    ]
    if missing_price:
        params = [p for p in params if p["name"] != "annual_net_price"]
    return ModelResponse(
        data={"summary": "Peak sales estimate.", "overall_confidence": 0.6,
              "parameters": params},
        input_tokens=120, output_tokens=60,
    )


def test_to_result_builds_param_claims_and_computed_range(tmp_path):
    module = PeakSalesModule()
    from memo.analysis import grounding
    labeled = grounding.gather_labeled_evidence(
        _ctx(tmp_path), ["disease epidemiology prevalence patients"]
    )
    good = next(iter(labeled))

    result = module._to_result("TEST", _canned_response(good), labeled)

    assert result.module == "peak_sales"
    assert result.summary == "Peak sales estimate."

    # One claim per parameter plus the computed peak-sales claim.
    assert len(result.claims) == 6
    computed = result.claims[-1]
    assert computed.value.startswith("$") and computed.value.endswith("B")
    assert "-" in computed.value  # it's a range

    # base = 100000 * 0.5 * 0.3 * 100000 * 0.4 = 600,000,000
    assert computed.rationale.__contains__("risk_adjusted base = $0.60B")

    # Grounded parameter claims carry evidence resolved from the retriever.
    pop_claim = next(c for c in result.claims if c.statement.startswith("epidemiology_population"))
    assert pop_claim.is_grounded
    assert pop_claim.evidence[0].url.startswith("https://example.com/")
    # ...and a basis tag reflecting the tier of the source it rests on (pubmed = grounded).
    assert "[basis: published literature, grounded]" in pop_claim.statement


def test_basis_from_evidence_tiers():
    # The parameter's tier is the best-tier source among its cited evidence; no evidence
    # is an assumption; an unknown source is a proxy, never fully grounded.
    from memo.analysis.base import Evidence
    from memo.analysis.peak_sales import _basis_from_evidence

    def _ev(source):
        return Evidence(doc_id="d", source=source, doc_type="t", url="u", quote="q", date=None)

    assert _basis_from_evidence([]) == ("not in corpus", "assumption")
    # Orphanet (grounded) beats CDC (proxy) when both are cited.
    label, tier = _basis_from_evidence([_ev("cdc"), _ev("orphanet")])
    assert tier == "grounded" and "Orphanet" in label
    # A generic floor alone is a proxy (a lower bound, not the net price).
    assert _basis_from_evidence([_ev("nadac")])[1] == "proxy"
    # An unrecognized source is treated as a proxy.
    assert _basis_from_evidence([_ev("mystery")])[1] == "proxy"


def test_to_result_flags_assumed_and_unknown_evidence(tmp_path):
    module = PeakSalesModule()
    from memo.analysis import grounding
    labeled = grounding.gather_labeled_evidence(
        _ctx(tmp_path), ["disease epidemiology prevalence patients"]
    )
    good = next(iter(labeled))

    result = module._to_result("TEST", _canned_response(good), labeled)

    # peak_penetration was flagged assumed; annual_net_price cited a bad id (E999) so it
    # has no valid evidence and is treated as an assumption too.
    pen_claim = next(c for c in result.claims if c.statement.startswith("peak_penetration"))
    assert "(ASSUMED)" in pen_claim.statement
    assert not pen_claim.is_grounded

    price_claim = next(c for c in result.claims if c.statement.startswith("annual_net_price"))
    assert "(ASSUMED)" in price_claim.statement
    assert not price_claim.is_grounded

    assert any("unknown evidence ids" in n for n in result.notes)
    # confidence discounted below the model's stated 0.6 for the two assumed params.
    assert 0 < result.confidence < 0.6
    assert result.usage == {"input_tokens": 120, "output_tokens": 60}


def test_to_result_defaults_missing_parameter(tmp_path):
    module = PeakSalesModule()
    from memo.analysis import grounding
    labeled = grounding.gather_labeled_evidence(
        _ctx(tmp_path), ["disease epidemiology prevalence patients"]
    )
    good = next(iter(labeled))

    result = module._to_result("TEST", _canned_response(good, missing_price=True), labeled)

    # Still five parameter claims + one computed claim; the omitted price is defaulted.
    assert len(result.claims) == 6
    price_claim = next(c for c in result.claims if c.statement.startswith("annual_net_price"))
    assert "ASSUMED" in price_claim.statement
    assert not price_claim.is_grounded
    assert any("missing from model output" in n for n in result.notes)
    assert any("parameters were absent" in n for n in result.notes)


def test_to_result_does_not_ground_net_price_on_per_unit_price(tmp_path):
    # A per-unit comparator price (CMS/NADAC) must NOT be used to ground annual net price:
    # a per-unit cost can't be annualized without a dosing schedule, and doing so produced
    # a spurious ~$0.14/patient/year that zeroed peak sales. Net price stays a flagged
    # assumption instead. (Grounding is gated off via _GROUND_NET_PRICE_ON_PER_UNIT.)
    ctx = _ctx(tmp_path)
    storage = Storage(root=tmp_path)
    storage.write_document(Document(
        company="TEST", source="cms", doc_type="spending", doc_id="Dupixent_2024",
        title="Medicare Part D spending: Dupixent (dupilumab), 2024",
        url="https://data.cms.gov/.../medicare-part-d-spending-by-drug?keyword=Dupixent",
        published="2024",
        metadata={
            "brand_name": "Dupixent", "generic_name": "dupilumab",
            "manufacturer": "Overall", "year": "2024",
            "avg_spending_per_dosage_unit": 250.0, "total_spending": 1_000_000_000.0,
        },
        text="Dupixent (dupilumab) net price per dosage unit, 2024.",
    ))
    price_records = StructuredStore("TEST", storage=storage).prices()
    assert price_records and price_records[0].price_per_unit == 250.0

    module = PeakSalesModule()
    from memo.analysis import grounding
    labeled = grounding.gather_labeled_evidence(
        ctx, ["disease epidemiology prevalence patients"]
    )
    good = next(iter(labeled))

    result = module._to_result("TEST", _canned_response(good), labeled, price_records)

    price_claim = next(
        c for c in result.claims if c.statement.startswith("annual_net_price")
    )
    # Not grounded on the per-unit record; stays the flagged assumption, and the absurd
    # per-unit value (250) is NOT what drives net price.
    assert not price_claim.is_grounded
    assert "ASSUMED" in price_claim.statement
    assert price_claim.value is None or "250" not in str(price_claim.value)
    # The per-unit comparator price does not reach the computed peak-sales claim.
    computed = result.claims[-1]
    assert not any(e.doc_id == "Dupixent_2024" for e in computed.evidence)


def test_analyze_returns_empty_when_no_evidence(tmp_path):
    module = PeakSalesModule()
    storage = Storage(root=tmp_path)  # empty: no documents ingested
    ctx = AnalysisContext(
        company="EMPTY",
        retriever=build_retriever("EMPTY", storage=storage),
        structured=StructuredStore("EMPTY", storage=storage),
        model=None,
    )
    result = module.analyze(ctx)
    assert result.claims == []
    assert result.confidence == 0.0
    assert any("no evidence" in n for n in result.notes)


# --------------------------------------------------------------- network (opt-in)


@pytest.mark.network
def test_peak_sales_end_to_end_real_model():
    """Full peak-sales path over real KYMR data. Skips without creds/data."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY set")
    if not (Storage().root / "KYMR").exists():
        pytest.skip("KYMR not ingested")

    from memo.analysis import AnthropicModelClient

    try:
        ctx = AnalysisContext.for_company("KYMR", model=AnthropicModelClient())
        result = PeakSalesModule().analyze(ctx)
    except Exception as exc:  # noqa: BLE001 - skip on auth/network trouble
        pytest.skip(f"model call unavailable: {exc}")

    assert result.summary
    computed = result.claims[-1]
    assert computed.value.startswith("$")
