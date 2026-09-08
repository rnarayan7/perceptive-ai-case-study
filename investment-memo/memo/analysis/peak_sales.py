"""Peak-sales analysis: the model picks and cites parameters, Python does the math.

Peak sales is a chain of multiplications over five numeric parameters
(epidemiology population, addressable fraction, peak penetration, annual net price,
probability of success). Letting a language model do that arithmetic invites silent
errors and unauditable point numbers, so this module splits the work in two:

- The model reads the retrieved evidence and returns ONLY the parameters, each cited
  back to evidence ids and flagged as an assumption when the corpus does not support it.
- :func:`estimate_peak_sales`, a pure deterministic function, does every multiplication
  and the low/base/high sensitivity range.

That separation is the whole point of the module and it is why the output is honest: our
corpus (SEC filings, ClinicalTrials.gov, PubMed) usually has thin epidemiology and no
drug pricing, so most runs lean on assumed inputs. Those are marked, confidence stays
low, and the headline number is always a range, never a precise point figure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from memo.analysis import grounding
from memo.analysis.base import AnalysisModule, AnalysisResult, Claim, Evidence
from memo.analysis.context import AnalysisContext
from memo.rag.structured import PriceRecord
from memo.rag.types import Chunk

# A price record is per dosage/pricing unit, but the model's net-price parameter is per
# patient per year. Converting between them needs a dosing schedule the corpus does not
# supply, so rather than invent a plausible-looking one (false precision) we annualize
# with an explicit, deliberately trivial factor of one unit per patient-year and surface
# that assumption in the claim rationale and the result notes. Raise this only when a
# grounded dosing figure is available.
_ASSUMED_ANNUAL_DOSING_UNITS = 1.0
# Grounding annual net price on a CMS/NADAC per-UNIT cost is unsound without a dosing
# schedule (units/patient/year), which the corpus does not provide. Doing so produced a
# spurious ~$0.14/patient/year figure that zeroed peak sales and rNPV. Until an annual
# net-price basis exists, keep net price as the flagged assumed default rather than a
# false "grounded" number. Flip to True only alongside a real annual-price source.
_GROUND_NET_PRICE_ON_PER_UNIT = False

# Ordered spec of the parameters the arithmetic needs: name -> (default, unit).
# Defaults are deliberately conservative and are only used (and flagged assumed) when the
# model omits a parameter, so the chain still computes rather than crashing.
_PARAM_SPECS: List[Tuple[str, float, str]] = [
    ("epidemiology_population", 100_000.0, "patients"),
    ("addressable_fraction", 0.5, "fraction"),
    ("peak_penetration", 0.15, "fraction"),
    ("annual_net_price", 100_000.0, "USD/patient/year"),
    ("probability_of_success", 0.2, "probability"),
]

_PARAM_NAMES = [name for name, _, _ in _PARAM_SPECS]
_DEFAULTS = {name: default for name, default, _ in _PARAM_SPECS}
_UNITS = {name: unit for name, _, unit in _PARAM_SPECS}


def estimate_peak_sales(
    params: Dict[str, float],
    sensitivity_margin: float = 0.2,
) -> Dict[str, Any]:
    """Compute peak sales from numeric parameters. Pure, deterministic, no model calls.

    Chain::

        treatable_population = epidemiology_population * addressable_fraction
        patients_on_drug     = treatable_population * peak_penetration
        gross_revenue        = patients_on_drug * annual_net_price
        risk_adjusted        = gross_revenue * probability_of_success

    The sensitivity range flexes the two most uncertain levers, penetration and price, by
    ``+/- sensitivity_margin`` together (low = both down, high = both up), holding the
    epidemiology, addressable fraction, and probability of success fixed. The base case
    always sits inside ``[low, high]``.

    Returns a dict with every intermediate value plus ``risk_adjusted`` as
    ``{"low", "base", "high"}`` and a formatted ``range_str`` like ``"$0.4-0.9B"``.
    """
    pop = float(params["epidemiology_population"])
    frac = float(params["addressable_fraction"])
    pen = float(params["peak_penetration"])
    price = float(params["annual_net_price"])
    pos = float(params["probability_of_success"])
    m = float(sensitivity_margin)

    treatable_population = pop * frac
    patients_on_drug = treatable_population * pen
    gross_revenue = patients_on_drug * price
    risk_adjusted = gross_revenue * pos

    def _risk_adjusted(pen_v: float, price_v: float) -> float:
        return treatable_population * pen_v * price_v * pos

    low = _risk_adjusted(pen * (1.0 - m), price * (1.0 - m))
    high = _risk_adjusted(pen * (1.0 + m), price * (1.0 + m))

    return {
        "treatable_population": treatable_population,
        "patients_on_drug": patients_on_drug,
        "gross_revenue": gross_revenue,
        "risk_adjusted": risk_adjusted,
        "sensitivity": {"low": low, "base": risk_adjusted, "high": high},
        "range_str": _format_range(low, high),
        "sensitivity_margin": m,
    }


def _format_billions(value: float) -> str:
    """Render a dollar figure in billions with one decimal, e.g. 8.64e8 -> '0.9'."""
    return f"{value / 1e9:.1f}"


def _format_range(low: float, high: float) -> str:
    """Format a risk-adjusted low/high as e.g. '$0.4-0.9B'."""
    return f"${_format_billions(low)}-{_format_billions(high)}B"


_SYSTEM = (
    "You are a biotech equity analyst extracting the NUMERIC PARAMETERS for a peak-sales "
    "model. You do NOT do any arithmetic: a separate deterministic calculator multiplies "
    "your parameters. Return only the parameters, never a revenue figure, and never "
    "multiply, add, or otherwise combine numbers yourself.\n"
    "Work only from the numbered evidence provided. For every parameter you must either "
    "cite one or more evidence ids (E1, E2, ...) that directly support the value, or, if "
    "the evidence does not support it, set assumed=true, give a clearly-labelled low-"
    "confidence estimate, and leave evidence_ids empty. Never present an unsupported "
    "number as if it were sourced.\n"
    "Extract these five parameters by name when the evidence allows: "
    "epidemiology_population (patient count for the disease), addressable_fraction "
    "(0-1, the share treatable/eligible given line of therapy), peak_penetration (0-1, "
    "peak market share among addressable patients), annual_net_price (USD per patient per "
    "year, net of discounts), probability_of_success (0-1, clinical/regulatory). Our "
    "corpus rarely contains drug pricing and often has thin epidemiology; when a "
    "parameter is not in the evidence, say so via assumed=true rather than inventing a "
    "citation. Calibrate confidence to the strength of the cited evidence."
)

# Each query pulls a different slice of the parameter set.
_QUERIES = [
    "disease epidemiology prevalence incidence number of patients",
    "addressable eligible patient population line of therapy treatment eligible",
    "drug pricing annual cost net price analog therapy wholesale acquisition cost",
    "market size revenue opportunity commercial potential",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "overall_confidence": {"type": "number"},
        "parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                    "assumed": {"type": "boolean"},
                },
                "required": [
                    "name",
                    "value",
                    "unit",
                    "evidence_ids",
                    "rationale",
                    "confidence",
                    "assumed",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "overall_confidence", "parameters"],
    "additionalProperties": False,
}


class PeakSalesModule(AnalysisModule):
    """Estimate peak sales: model cites parameters, :func:`estimate_peak_sales` computes."""

    name = "peak_sales"

    def __init__(
        self,
        k_per_query: int = 6,
        max_evidence: int = 14,
        sensitivity_margin: float = 0.2,
    ) -> None:
        self.k_per_query = k_per_query
        self.max_evidence = max_evidence
        self.sensitivity_margin = sensitivity_margin

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        labeled = grounding.gather_labeled_evidence(
            context, _QUERIES, k_per_query=self.k_per_query, max_evidence=self.max_evidence
        )
        if not labeled:
            return AnalysisResult(
                company=context.company,
                module=self.name,
                summary="",
                confidence=0.0,
                notes=["no evidence retrieved; cannot estimate peak sales"],
            )

        user = self._build_prompt(context.company, labeled)
        response = context.model.complete_json(_SYSTEM, user, _SCHEMA)

        # Comparator prices arrive already structured; prefer them over the model's
        # assumed net price. Empty today for KYMR (no comparator prices ingested yet),
        # in which case _to_result falls back to the model-assumed parameter.
        price_records = list(context.structured.prices())
        price_records.extend(context.structured.acquisition_costs())
        return self._to_result(context.company, response, labeled, price_records)

    # ---------------------------------------------------------------- internals

    def _build_prompt(self, company: str, labeled: Dict[str, Chunk]) -> str:
        lines = [
            f"Company: {company}",
            "",
            grounding.format_evidence_block(labeled),
            "",
            "Task: extract the numeric parameters for a peak-sales model. Return only the "
            "parameters (epidemiology_population, addressable_fraction, peak_penetration, "
            "annual_net_price, probability_of_success), each citing its evidence ids or "
            "flagged assumed=true when the evidence does not support it. Do not compute "
            "revenue; a deterministic calculator will multiply your parameters.",
        ]
        return "\n".join(lines)

    def _to_result(
        self,
        company: str,
        response: Any,
        labeled: Dict[str, Chunk],
        price_records: Optional[List[PriceRecord]] = None,
    ) -> AnalysisResult:
        data = response.data
        notes: List[str] = []
        claims: List[Claim] = []

        # Only positive, real prices can ground the net-price input; anything else falls
        # back to the model-assumed parameter (the common case until comparators exist).
        groundable_prices = [r for r in (price_records or []) if r.price_per_unit > 0]

        # Index the model's parameters by name (last one wins on duplicates).
        raw_by_name: Dict[str, Dict[str, Any]] = {}
        for raw in data.get("parameters", []):
            name = raw.get("name", "")
            if name in _DEFAULTS:
                raw_by_name[name] = raw
            else:
                notes.append(f"model returned unrecognized parameter '{name}'; ignored")

        values: Dict[str, float] = {}
        all_evidence: List[Evidence] = []
        assumed_names: List[str] = []
        missing_names: List[str] = []

        for name in _PARAM_NAMES:
            # Net price: grounding on a per-unit comparator price is disabled (see
            # _GROUND_NET_PRICE_ON_PER_UNIT) because per-unit costs can't be annualized
            # without a dosing schedule; net price stays a flagged assumption instead.
            if name == "annual_net_price" and groundable_prices and _GROUND_NET_PRICE_ON_PER_UNIT:
                value, claim, evidence, note = self._grounded_net_price(groundable_prices)
                values[name] = value
                all_evidence.extend(evidence)
                notes.append(note)
                claims.append(claim)
                continue

            raw = raw_by_name.get(name)
            if raw is None:
                # Model omitted it entirely: fall back to a flagged default assumption.
                value = _DEFAULTS[name]
                values[name] = value
                missing_names.append(name)
                assumed_names.append(name)
                notes.append(
                    f"parameter '{name}' missing from model output; "
                    f"defaulted to {value} ({_UNITS[name]}) as a low-confidence assumption"
                )
                claims.append(
                    Claim(
                        statement=f"{name} = {value} {_UNITS[name]} (ASSUMED, not in corpus)",
                        confidence=0.1,
                        rationale=(
                            "No parameter returned by the model; using a conservative "
                            "default so the estimate can be computed."
                        ),
                        evidence=[],
                        value=f"{value} {_UNITS[name]}",
                    )
                )
                continue

            value = float(raw.get("value", _DEFAULTS[name]))
            values[name] = value
            unit = raw.get("unit") or _UNITS[name]
            evidence, unknown = grounding.resolve_evidence(
                raw.get("evidence_ids", []), labeled
            )
            if unknown:
                notes.append(
                    f"parameter '{name}' cited unknown evidence ids {unknown}; dropped those"
                )

            assumed = bool(raw.get("assumed", False)) or not evidence
            if assumed:
                assumed_names.append(name)
                if not evidence and not raw.get("assumed", False):
                    notes.append(
                        f"parameter '{name}' has no valid evidence; treated as assumption"
                    )

            all_evidence.extend(evidence)
            flag = " (ASSUMED)" if assumed else ""
            claims.append(
                Claim(
                    statement=f"{name} = {value} {unit}{flag}",
                    confidence=float(raw.get("confidence", 0.0)),
                    rationale=raw.get("rationale", ""),
                    evidence=evidence,
                    value=f"{value} {unit}",
                )
            )

        estimate = estimate_peak_sales(values, sensitivity_margin=self.sensitivity_margin)

        # De-dupe evidence for the computed claim by chunk_id so it stays auditable.
        seen_ids: set = set()
        computed_evidence: List[Evidence] = []
        for ev in all_evidence:
            key = ev.chunk_id or ev.doc_id
            if key in seen_ids:
                continue
            seen_ids.add(key)
            computed_evidence.append(ev)

        assumption_note = ""
        if assumed_names:
            assumption_note = (
                f" Rests on assumed parameters ({', '.join(assumed_names)}); "
                "treat as an order-of-magnitude range, not a point estimate."
            )
        computed_claim = Claim(
            statement=(
                "Risk-adjusted peak sales (sensitivity range on penetration and price)."
                + assumption_note
            ),
            confidence=self._computed_confidence(data, assumed_names),
            rationale=(
                f"treatable_population = {estimate['treatable_population']:.0f}; "
                f"patients_on_drug = {estimate['patients_on_drug']:.0f}; "
                f"gross_revenue = ${estimate['gross_revenue'] / 1e9:.2f}B; "
                f"risk_adjusted base = ${estimate['risk_adjusted'] / 1e9:.2f}B. "
                "Arithmetic done deterministically in code from the cited parameters."
            ),
            evidence=computed_evidence,
            value=estimate["range_str"],
        )
        claims.append(computed_claim)

        if missing_names:
            notes.append(
                "confidence lowered: "
                f"{len(missing_names)} of {len(_PARAM_NAMES)} parameters were absent"
            )

        return AnalysisResult(
            company=company,
            module=self.name,
            summary=data.get("summary", ""),
            claims=claims,
            confidence=self._computed_confidence(data, assumed_names),
            notes=notes,
            usage=response.usage,
        )

    def _grounded_net_price(
        self, prices: List[PriceRecord]
    ) -> Tuple[float, Claim, List[Evidence], str]:
        """Build a GROUNDED annual_net_price claim from a real comparator price record.

        Picks the most defensible record (CMS near-net price preferred over NADAC
        acquisition cost, then the most recent period), cites its source document, and
        annualizes with the explicit ``_ASSUMED_ANNUAL_DOSING_UNITS`` factor. The price
        itself is sourced; only the per-unit -> per-year conversion is assumed, and that
        assumption is stated in both the rationale and the returned note.
        """
        record = self._best_price_record(prices)
        unit_price = float(record.price_per_unit)
        value = unit_price * _ASSUMED_ANNUAL_DOSING_UNITS
        unit = _UNITS["annual_net_price"]

        origin = record.drug + (f" ({record.generic})" if record.generic else "")
        period = f", {record.period}" if record.period else ""
        quote = (
            f"{origin}: {record.source.upper()} net price ${unit_price:,.5g} per "
            f"{record.unit}{period}."
        )
        evidence = [
            Evidence(
                doc_id=record.doc_id,
                source=record.source,
                doc_type=record.doc_type,
                url=record.url,
                quote=quote,
                date=record.period,
            )
        ]
        rationale = (
            f"Grounded on the {record.source.upper()} net price for comparator {origin} "
            f"(${unit_price:,.5g} per {record.unit}{period}). Annualized as "
            f"{_ASSUMED_ANNUAL_DOSING_UNITS:g} {record.unit}(s) per patient/year, an "
            "explicit assumption (no grounded dosing schedule); the per-unit price is "
            "cited, the annual conversion is not."
        )
        claim = Claim(
            statement=f"annual_net_price = {value} {unit} (GROUNDED on {record.source} price)",
            confidence=0.5,
            rationale=rationale,
            evidence=evidence,
            value=f"{value} {unit}",
        )
        note = (
            f"parameter 'annual_net_price' grounded on {record.source} comparator price "
            f"${unit_price:,.5g} per {record.unit} ({record.url}); annualized with an "
            f"explicit assumed {_ASSUMED_ANNUAL_DOSING_UNITS:g} {record.unit}(s)/patient/"
            "year (price sourced, conversion assumed)"
        )
        return value, claim, evidence, note

    @staticmethod
    def _best_price_record(prices: List[PriceRecord]) -> PriceRecord:
        """Prefer a CMS near-net price over NADAC acquisition cost, then most recent.

        Period strings (``"2024"`` or ``"2026-01-01"``) sort lexicographically, so the
        max picks the newest within the preferred source pool.
        """
        cms = [r for r in prices if r.source == "cms"]
        pool = cms or prices
        return max(pool, key=lambda r: r.period or "")

    @staticmethod
    def _computed_confidence(data: Dict[str, Any], assumed_names: List[str]) -> float:
        """Model confidence, discounted for every assumed/missing parameter."""
        base = float(data.get("overall_confidence", 0.0))
        # Each assumed parameter knocks the confidence down multiplicatively.
        penalty = 0.7 ** len(assumed_names)
        return round(max(0.0, min(1.0, base)) * penalty, 4)
