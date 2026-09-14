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

# Net price is never grounded on a per-unit comparator cost: annualizing it needs a
# dosing schedule (units/patient/year) the corpus lacks, and doing so once produced a
# spurious ~$0.14/patient/year that zeroed peak sales and rNPV. The best available
# comparator anchor is surfaced as a note instead (see analyze); net price stays a
# flagged assumption unless the model cites a real annual figure.

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

# Optional grounded input, not one of the required five: an absolute company-disclosed
# eligible/addressable patient count. When the model returns it CITED, estimate_peak_sales
# uses it as the treatable pool directly, replacing epidemiology_population x
# addressable_fraction (two assumed/estimated inputs) with one sourced number.
_ADDRESSABLE_POP = "addressable_population"

# Basis labeling: every parameter carries the BASIS its number rests on, so a reader sees
# whether it is a real anchor or a guess. tier is one of:
#   grounded  - a directly sourced figure (net price, a real prevalence series)
#   proxy     - a related stand-in (a generic price floor as a lower bound, a proxy
#               indication's prevalence) that is sourced but not the exact quantity
#   assumption- no supporting evidence in the corpus
# The label names the specific basis; the source of a parameter's cited evidence decides
# its tier via _SOURCE_BASIS.
_SOURCE_BASIS: Dict[str, Tuple[str, str]] = {
    "asp": ("Medicare Part B ASP net price", "grounded"),
    "cms": ("CMS Part D net price", "grounded"),
    "nadac": ("NADAC generic price floor (lower bound)", "proxy"),
    "orphanet": ("Orphanet rare-disease prevalence", "grounded"),
    "cdc": ("CDC prevalence (proxy indication)", "proxy"),
    "pubmed": ("published literature", "grounded"),
    "preprints": ("preprint literature", "proxy"),
    "clinicaltrials": ("trial disclosure", "grounded"),
    "edgar": ("company SEC disclosure", "grounded"),
    "xbrl": ("company XBRL financials", "grounded"),
    "openfda": ("FDA label / approval", "grounded"),
    "pubchem": ("PubChem chemistry", "grounded"),
}
_ASSUMPTION_BASIS = ("not in corpus", "assumption")
_TIER_RANK = {"grounded": 2, "proxy": 1, "assumption": 0}

# Sources that carry disease epidemiology (prevalence / incidence / patient counts). The
# addressable population should rest on one of these for the LEAD indication; a population
# cited only to non-epi sources (or to nothing) is flagged so a reader knows the rNPV
# denominator is not epidemiology-grounded.
EPI_SOURCES = frozenset({"cdc", "pubmed", "orphanet", "preprints"})

# Plausibility floor for a lead commercial indication's eligible population. A value below
# this that is NOT grounded on a rare-disease prevalence source (Orphanet) is almost
# certainly a niche sub-indication mistaken for the lead market. This is the PRAX bug: the
# model returned 4,000 ("rare pediatric epilepsy") when the lead asset (vormatrigine)
# targets focal epilepsy, a market of hundreds of thousands. We flag it, never silently
# overwrite the number (that would be false precision); the flag lowers confidence and
# tells a reader to re-check the indication.
POP_PLAUSIBILITY_FLOOR = 50_000.0

# Plausibility CEILING, symmetric to the floor: no single US commercial indication's
# addressable population credibly exceeds this. A value above it is almost certainly an
# over-aggregation -- several related indications summed into one number. This is the KYMR
# bug: ~16.5M by lumping atopic dermatitis with asthma/COPD/EoE/CRSwNP, which inflated peak
# sales to ~$23B on a Phase 1 asset. We flag it (never overwrite) and the flag lowers
# confidence. 50M US is a deliberately high bound so it only trips on clear aggregation.
POP_PLAUSIBILITY_CEILING = 50_000_000.0

# Deterministic over-aggregation detector. The population must be ONE lead indication's
# eligible patients; a rationale that names several distinct indications joined by summing
# language is aggregating markets that should be sized separately. Synonyms collapse to a
# single concept so "EoE" and "eosinophilic esophagitis" are not double counted.
_INDICATION_CONCEPTS: Dict[str, str] = {
    "atopic dermatitis": "atopic dermatitis",
    "eczema": "atopic dermatitis",
    "asthma": "asthma",
    "copd": "copd",
    "chronic obstructive": "copd",
    "eosinophilic esophagitis": "eoe",
    "eoe": "eoe",
    "chronic rhinosinusitis": "crswnp",
    "nasal polyps": "crswnp",
    "crswnp": "crswnp",
    "prurigo nodularis": "prurigo nodularis",
    "ulcerative colitis": "ulcerative colitis",
    "crohn": "crohn disease",
    "essential tremor": "essential tremor",
    "epilepsy": "epilepsy",
    "seizure": "epilepsy",
    "graves": "graves disease",
    "thyroid eye": "thyroid eye disease",
    "myasthenia": "myasthenia gravis",
    "systemic mastocytosis": "mastocytosis",
    "mastocytosis": "mastocytosis",
    "gastrointestinal stromal": "gist",
    "gist": "gist",
    "hidradenitis": "hidradenitis suppurativa",
    "psoriasis": "psoriasis",
    "rheumatoid arthritis": "rheumatoid arthritis",
    "lupus": "lupus",
    "alopecia": "alopecia areata",
}
# Summation/umbrella cues that turn "several indications named" into "several indications
# aggregated into one number". Two distinct indications plus one of these trips the flag.
_AGGREGATION_CUES = (
    " + ",
    "+",
    " plus ",
    " combined",
    "aggregate",
    "sum of",
    "summed",
    "as well as",
    "along with",
    "together with",
    "related indication",
    "related immunolog",
    "multiple indication",
    "across indication",
    "and related",
    "illustrative",
    "umbrella",
)

# Source precedence for a comparator net-price anchor: a Part B ASP net price beats a
# Part D near-net price, which beats a generic acquisition-cost floor.
_PRICE_SOURCE_RANK = {"asp": 3, "cms": 2, "nadac": 1}


def _distinct_indications(text: str) -> List[str]:
    """Distinct disease concepts named in ``text`` (synonyms collapsed)."""
    lowered = text.lower()
    return sorted({concept for term, concept in _INDICATION_CONCEPTS.items() if term in lowered})


def _detect_over_aggregation(text: str) -> Tuple[bool, List[str]]:
    """Flag a population whose text sums/aggregates several distinct indications.

    Returns ``(over_aggregated, notes)``. Trips only when the text names two or more
    distinct indications AND carries explicit aggregation language, so a single indication
    (even one whose rationale mentions a differential diagnosis in passing) is not flagged.
    """
    lowered = (text or "").lower()
    if not lowered.strip():
        return False, []
    distinct = _distinct_indications(lowered)
    has_cue = any(cue in lowered for cue in _AGGREGATION_CUES)
    if len(distinct) >= 2 and has_cue:
        return True, [
            "SANITY FLAG: epidemiology_population text aggregates multiple distinct "
            "indications (" + ", ".join(distinct) + ") with summation language; a lead "
            "commercial indication must be sized on its own -- do not sum related "
            "indications into one population. Pick the single most material indication and "
            "size THAT one; note the others qualitatively in prose, not in the number"
        ]
    return False, []


def _basis_from_evidence(evidence: List[Evidence]) -> Tuple[str, str]:
    """Label a parameter by the best-tier source among its cited evidence.

    Returns ``(basis_label, tier)``. With no evidence the parameter is an assumption.
    When evidence spans several sources the highest tier wins (a grounded figure beats a
    proxy), and that source's basis label is used. An unrecognized source is treated as a
    proxy so an unknown origin never reads as fully grounded.
    """
    best: Optional[Tuple[str, str]] = None
    for ev in evidence:
        basis = _SOURCE_BASIS.get(ev.source, (ev.source or "unknown source", "proxy"))
        if best is None or _TIER_RANK[basis[1]] > _TIER_RANK[best[1]]:
            best = basis
    return best or _ASSUMPTION_BASIS


def _basis_tag(basis_label: str, tier: str) -> str:
    """Render a basis label for a claim statement, e.g. ``[basis: CDC prevalence, proxy]``."""
    return f"[basis: {basis_label}, {tier}]"


def epidemiology_population_flags(
    value: float, evidence: List[Evidence], text: str = ""
) -> Tuple[bool, bool, List[str]]:
    """Grounding + magnitude flags for the addressable-population parameter.

    Returns ``(cited_epi_source, plausible_magnitude, notes)``:

    - ``cited_epi_source`` - at least one cited evidence item is an epidemiology source
      (:data:`EPI_SOURCES`), i.e. the population rests on prevalence/incidence evidence
      rather than a company aside or a free guess.
    - ``plausible_magnitude`` - the value sits in a sane band for a SINGLE lead indication:
      at or above :data:`POP_PLAUSIBILITY_FLOOR` (or grounded on a rare-disease source,
      where a small count is legitimate), at or below :data:`POP_PLAUSIBILITY_CEILING`, and
      not an over-aggregation of several indications when ``text`` (the population rationale)
      is supplied.

    The check is symmetric: the floor catches UNDERSTATEMENT (the PRAX bug -- a tiny,
    ungrounded population for a broad lead indication) and the ceiling plus the
    over-aggregation scan catch OVERSTATEMENT (the KYMR bug -- several related indications
    summed into one inflated number). Every flag is reported in ``notes``; the model's
    number is never overwritten.
    """
    sources = {e.source for e in evidence}
    epi_hits = sources & EPI_SOURCES
    cited_epi = bool(epi_hits)
    grounded_on_rare = "orphanet" in sources
    above_floor = value >= POP_PLAUSIBILITY_FLOOR or grounded_on_rare
    below_ceiling = value <= POP_PLAUSIBILITY_CEILING
    over_aggregated, agg_notes = _detect_over_aggregation(text)
    plausible = above_floor and below_ceiling and not over_aggregated

    notes: List[str] = []
    if cited_epi:
        notes.append(
            "epidemiology_population grounded on epidemiology source(s): "
            + ", ".join(sorted(epi_hits))
        )
    else:
        notes.append(
            "epidemiology_population is NOT cited to an epidemiology source "
            "(CDC/PubMed/Orphanet/preprints); the addressable population is not "
            "epidemiology-grounded, so treat the rNPV denominator as an assumption"
        )
    if not above_floor:
        notes.append(
            f"SANITY FLAG: epidemiology_population = {value:,.0f} is implausibly small for "
            "a lead commercial indication and is not grounded on a rare-disease prevalence "
            "source; this is likely a niche sub-indication mistaken for the lead market -- "
            "re-check the lead commercial indication and source its prevalence"
        )
    if not below_ceiling:
        notes.append(
            f"SANITY FLAG: epidemiology_population = {value:,.0f} is implausibly large for a "
            f"single lead commercial indication (> {POP_PLAUSIBILITY_CEILING:,.0f}); verify it "
            "is ONE indication's addressable patients and not several indications aggregated "
            "into one number"
        )
    notes.extend(agg_notes)
    return cited_epi, plausible, notes


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

    # A company-disclosed absolute eligible/addressable count, when present, is the treatable
    # pool directly: one sourced number in place of prevalence x an assumed eligibility share.
    addressable_population = params.get("addressable_population")
    if addressable_population and float(addressable_population) > 0:
        treatable_population = float(addressable_population)
    else:
        treatable_population = pop * frac
    patients_on_drug = treatable_population * pen
    gross_revenue = patients_on_drug * price
    risk_adjusted = gross_revenue * pos  # kept for reference; PoS is applied in valuation

    # range_str is GROSS peak sales (how peak sales is normally quoted). Risk adjustment
    # (x PoS) happens exactly once, in compute_rnpv; applying it here too double-counts
    # PoS and understates the rNPV by a factor of ~PoS.
    def _gross(pen_v: float, price_v: float) -> float:
        return treatable_population * pen_v * price_v

    low = _gross(pen * (1.0 - m), price * (1.0 - m))
    high = _gross(pen * (1.0 + m), price * (1.0 + m))

    return {
        "treatable_population": treatable_population,
        "patients_on_drug": patients_on_drug,
        "gross_revenue": gross_revenue,
        "risk_adjusted": risk_adjusted,
        "sensitivity": {"low": low, "base": gross_revenue, "high": high},
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
    "FIRST identify the LEAD COMMERCIAL indication: the disease the company's lead / most-"
    "advanced asset is developed for and that defines the largest addressable market. Return "
    "it in lead_indication. Do NOT anchor on a narrow rare or pediatric sub-indication that "
    "merely appears in the evidence when a broader indication is the commercial driver; a "
    "company can run several programs, but the peak-sales denominator must be the lead "
    "indication's population, not a niche program's.\n"
    "Extract these five parameters by name when the evidence allows: "
    "epidemiology_population (patient count for the disease), addressable_fraction "
    "(0-1, the share treatable/eligible given line of therapy), peak_penetration (0-1, "
    "peak market share among addressable patients), annual_net_price (USD per patient per "
    "year, net of discounts), probability_of_success (0-1, clinical/regulatory).\n"
    "epidemiology_population MUST be the eligible patient count for the LEAD indication you "
    "named, GROUNDED IN and CITING retrieved epidemiology evidence (CDC prevalence, "
    "PubMed/preprint literature, or Orphanet) for that indication. If no retrieved evidence "
    "gives a prevalence or patient count for the lead indication, set assumed=true, "
    "leave evidence_ids empty, and give a plausible order-of-magnitude estimate for the "
    "LEAD indication's size -- never substitute a niche sub-population to look grounded. "
    "MATCH THE NUMBER TO THE NAMED INDICATION, AND USE PREVALENCE. The population MUST be for "
    "the exact disease you put in lead_indication. Do NOT ground it on a DIFFERENT disease's "
    "figure just because that disease has a cleaner or single number in the evidence: e.g. do "
    "NOT use a myasthenia gravis count for a Graves' disease lead, or a gastrointestinal "
    "stromal tumor count for a systemic mastocytosis lead. Prefer POINT PREVALENCE (the number "
    "of living patients) over ANNUAL INCIDENCE (new cases per year); annual incidence badly "
    "undercounts the treatable pool, so never pass an incidence figure through as the "
    "addressable population. If the only figure retrieved for your NAMED lead indication is an "
    "incidence, or the only clean prevalence in the evidence belongs to a DIFFERENT disease, "
    "set assumed=true with a plausible prevalence estimate for the lead indication rather than "
    "citing the wrong-indication or wrong-measure number -- a clean number for the wrong "
    "disease is NOT grounding. "
    "PREFER A COMPANY-DISCLOSED ELIGIBLE COUNT. If the company's own filings or the retrieved "
    "evidence state an ABSOLUTE number of eligible / addressable / target patients for the "
    "lead indication (for example 'approximately 20,000 to 35,000 US patients with "
    "moderate-to-severe X not adequately controlled'), return it as an EXTRA parameter named "
    "addressable_population (an absolute patient count, unit patients) CITED to that evidence. "
    "When you provide a grounded addressable_population the calculator uses it directly as the "
    "treatable pool and skips epidemiology_population x addressable_fraction, so a disclosed "
    "eligible count is strongly preferred over multiplying a prevalence by an assumed "
    "eligibility share. Still return epidemiology_population and addressable_fraction as the "
    "fallback; if you have no CITED eligible count, omit addressable_population entirely "
    "rather than guessing one. "
    "epidemiology_population MUST be exactly ONE lead commercial indication's addressable "
    "patients. Do NOT sum, add, or aggregate several related indications into one number "
    "(e.g. do not lump atopic dermatitis with asthma, COPD, EoE, or CRSwNP). If several "
    "indications are plausible, pick the SINGLE most material one and size THAT indication "
    "only; mention the other programs qualitatively in the summary, never inside the "
    "population number. Sanity-check magnitude in BOTH directions: a common/broad indication "
    "(e.g. epilepsy, asthma, atopic dermatitis) has hundreds of thousands to a few million "
    "patients; a few thousand is only credible for a genuinely ultra-rare disease, and a "
    "figure above ~50 million US patients is almost always several indications wrongly summed "
    "together.\n"
    "Our corpus rarely contains drug pricing and often has thin epidemiology; when a "
    "parameter is not in the evidence, say so via assumed=true rather than inventing a "
    "citation. Calibrate confidence to the strength of the cited evidence."
)

# Each query pulls a different slice of the parameter set. The first pins the LEAD
# indication (so the population is sized to it, not a niche program); the next two lean
# hard on epidemiology terms so CDC/PubMed prevalence evidence for the treated disease is
# surfaced when it exists, rather than only trial or pipeline text.
_QUERIES = [
    "lead product candidate most advanced program primary target indication disease treated",
    "disease epidemiology prevalence incidence patients diagnosed population United States",
    "people living with the disease annual new cases eligible patient population prevalence",
    "eligible addressable target patient population moderate to severe not adequately "
    "controlled inadequate response refractory number of US patients we estimate",
    "drug pricing annual cost net price analog therapy wholesale acquisition cost",
    "market size revenue opportunity commercial potential total addressable market",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "lead_indication": {
            "type": "string",
            "description": (
                "The single lead commercial indication whose addressable patients define "
                "epidemiology_population. Name one disease, not a bundle of related "
                "indications."
            ),
        },
        "overall_confidence": {"type": "number"},
        "parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {
                        "type": "number",
                        "description": (
                            "The parameter's numeric value. For epidemiology_population this "
                            "is exactly ONE lead indication's addressable patient count -- "
                            "never several related indications summed together."
                        ),
                    },
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
    "required": ["summary", "lead_indication", "overall_confidence", "parameters"],
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

        # Comparator prices arrive already structured; the best-basis one anchors the
        # net-price reference (ASP net > CMS near-net > NADAC generic floor). Any source
        # may be empty for a given company, in which case the pool is just the others.
        price_records = list(context.structured.asp_prices())
        price_records.extend(context.structured.prices())
        price_records.extend(context.structured.acquisition_costs())
        return self._to_result(context.company, response, labeled, price_records)

    # ---------------------------------------------------------------- internals

    def _build_prompt(self, company: str, labeled: Dict[str, Chunk]) -> str:
        lines = [
            f"Company: {company}",
            "",
            grounding.format_evidence_block(labeled),
            "",
            "Task: first name the LEAD COMMERCIAL indication (lead_indication) from the "
            "pipeline/company evidence -- the disease the lead asset targets and that sets "
            "the largest addressable market, not a niche sub-program. Then extract the "
            "numeric parameters for a peak-sales model (epidemiology_population, "
            "addressable_fraction, peak_penetration, annual_net_price, "
            "probability_of_success), each citing its evidence ids or flagged assumed=true "
            "when the evidence does not support it. epidemiology_population must be the "
            "eligible patient count for the lead indication, cited to retrieved epidemiology "
            "evidence (CDC/PubMed/Orphanet) for that indication or flagged assumed=true with "
            "a plausible order-of-magnitude size -- never a niche sub-population. Do not "
            "compute revenue; a deterministic calculator will multiply your parameters.",
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

        # The lead commercial indication the population must be sized to. Surfaced up front
        # so a reader (and the epi-grounding eval) can see which market the denominator
        # claims to represent.
        lead_indication = str(data.get("lead_indication", "")).strip()
        if lead_indication:
            notes.append(f"lead commercial indication (model): {lead_indication}")

        # Evidence actually resolved for each parameter, so the epidemiology_population
        # grounding/magnitude sanity check below reasons over real cited sources.
        param_evidence: Dict[str, List[Evidence]] = {}

        # Only positive, real prices can ground the net-price input; anything else falls
        # back to the model-assumed parameter (the common case until comparators exist).
        groundable_prices = [r for r in (price_records or []) if r.price_per_unit > 0]

        # Surface the best comparator anchor and its basis so the lower-bound-vs-net
        # distinction is visible: a NADAC generic floor is a lower bound, a CMS/ASP figure
        # is a net-price reference. The per-unit price is not annualized into the value
        # (that needs a dosing schedule), so it informs the reader without setting net price.
        if groundable_prices:
            ref = self._best_price_record(groundable_prices)
            ref_basis, ref_tier = _SOURCE_BASIS.get(ref.source, (ref.source, "proxy"))
            kind = "lower-bound floor" if ref_tier == "proxy" else "net-price reference"
            notes.append(
                f"comparator {kind} available: {ref_basis} for {ref.drug} "
                f"${ref.price_per_unit:,.5g} per {ref.unit} ({ref.source}); annual_net_price "
                "left assumed pending a dosing schedule (per-unit price not annualized)"
            )

        # Index the model's parameters by name (last one wins on duplicates).
        raw_by_name: Dict[str, Dict[str, Any]] = {}
        for raw in data.get("parameters", []):
            name = raw.get("name", "")
            if name in _DEFAULTS or name == _ADDRESSABLE_POP:
                raw_by_name[name] = raw
            else:
                notes.append(f"model returned unrecognized parameter '{name}'; ignored")

        values: Dict[str, float] = {}
        all_evidence: List[Evidence] = []
        assumed_names: List[str] = []
        missing_names: List[str] = []

        for name in _PARAM_NAMES:
            raw = raw_by_name.get(name)
            if raw is None:
                # Model omitted it entirely: fall back to a flagged default assumption.
                value = _DEFAULTS[name]
                values[name] = value
                param_evidence[name] = []
                missing_names.append(name)
                assumed_names.append(name)
                notes.append(
                    f"parameter '{name}' missing from model output; "
                    f"defaulted to {value} ({_UNITS[name]}) as a low-confidence assumption"
                )
                claims.append(
                    Claim(
                        statement=(
                            f"{name} = {value} {_UNITS[name]} (ASSUMED, not in corpus) "
                            f"{_basis_tag(*_ASSUMPTION_BASIS)}"
                        ),
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
            param_evidence[name] = evidence
            # The basis reflects the source the parameter actually rests on; with no
            # evidence it is an assumption regardless of what the model claimed.
            basis_label, tier = _basis_from_evidence(evidence)
            flag = " (ASSUMED)" if assumed else ""
            claims.append(
                Claim(
                    statement=f"{name} = {value} {unit}{flag} {_basis_tag(basis_label, tier)}",
                    confidence=float(raw.get("confidence", 0.0)),
                    rationale=raw.get("rationale", ""),
                    evidence=evidence,
                    value=f"{value} {unit}",
                )
            )

        # Deterministic epidemiology sanity check on the addressable population: is it cited
        # to a real epidemiology source, and is its magnitude plausible for a lead commercial
        # indication? Flags (not overwrites) a tiny/ungrounded population, which is what
        # produced the bad PRAX rNPV. An ungrounded population also counts as assumed so the
        # confidence penalty applies.
        # The over-aggregation scan reads the model's own rationale for the population plus
        # the named lead indication, so a rationale that sums several indications is caught.
        epi_raw = raw_by_name.get("epidemiology_population", {})
        epi_text = " ".join(
            str(part)
            for part in (lead_indication, epi_raw.get("rationale", ""), epi_raw.get("name", ""))
            if part
        )
        cited_epi, plausible_pop, epi_notes = epidemiology_population_flags(
            values.get("epidemiology_population", 0.0),
            param_evidence.get("epidemiology_population", []),
            text=epi_text,
        )
        notes.extend(epi_notes)
        if (not cited_epi or not plausible_pop) and "epidemiology_population" not in assumed_names:
            assumed_names.append("epidemiology_population")

        # Optional: a company-disclosed absolute eligible/addressable count. When grounded it
        # replaces epidemiology_population x addressable_fraction as the treatable pool (one
        # sourced number in place of two assumed inputs). Ignored unless it carries evidence.
        addr_raw = raw_by_name.get(_ADDRESSABLE_POP)
        if addr_raw is not None:
            addr_val = float(addr_raw.get("value", 0.0) or 0.0)
            addr_ev, _unknown = grounding.resolve_evidence(addr_raw.get("evidence_ids", []), labeled)
            grounded = addr_val > 0 and bool(addr_ev) and not bool(addr_raw.get("assumed", False))
            if grounded:
                values[_ADDRESSABLE_POP] = addr_val
                all_evidence.extend(addr_ev)
                param_evidence[_ADDRESSABLE_POP] = addr_ev
                basis_label, tier = _basis_from_evidence(addr_ev)
                claims.append(
                    Claim(
                        statement=(
                            f"addressable_population = {addr_val:.0f} patients "
                            f"(company-disclosed eligible count) {_basis_tag(basis_label, tier)}"
                        ),
                        confidence=float(addr_raw.get("confidence", 0.0)),
                        rationale=addr_raw.get("rationale", ""),
                        evidence=addr_ev,
                        value=f"{addr_val:.0f} patients",
                    )
                )
                notes.append(
                    "treatable pool grounded to a company-disclosed addressable/eligible "
                    "patient count; epidemiology_population x addressable_fraction bypassed"
                )
            else:
                notes.append(
                    "addressable_population returned but not grounded; ignored, using "
                    "epidemiology_population x addressable_fraction"
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
                "Gross peak sales (sensitivity range on penetration and price); the "
                "PoS risk adjustment is applied in the valuation, not here."
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

    @staticmethod
    def _best_price_record(prices: List[PriceRecord]) -> PriceRecord:
        """Pick the best-basis comparator price: ASP net > CMS near-net > NADAC floor.

        Within the highest-ranked source present, take the most recent record. Period
        strings (``"2024"`` or ``"2026-01-01"``) sort lexicographically, so the max picks
        the newest in that pool.
        """
        best_rank = max(_PRICE_SOURCE_RANK.get(r.source, 0) for r in prices)
        pool = [r for r in prices if _PRICE_SOURCE_RANK.get(r.source, 0) == best_rank]
        return max(pool, key=lambda r: r.period or "")

    @staticmethod
    def _computed_confidence(data: Dict[str, Any], assumed_names: List[str]) -> float:
        """Model confidence, discounted for every assumed/missing parameter."""
        base = float(data.get("overall_confidence", 0.0))
        # Each assumed parameter knocks the confidence down multiplicatively.
        penalty = 0.7 ** len(assumed_names)
        return round(max(0.0, min(1.0, base)) * penalty, 4)
