"""Per-company profiles: the query terms each source needs to return anything.

Several ingestion sources cannot be driven from a ticker alone. openFDA, CMS Part D
and NADAC index *marketed* drugs, and every company here is development-stage, so a
ticker matches nothing. What those sources can say is what the comparator landscape
looks like, which is exactly what the peak-sales and pricing analysis needs. To get
that, each source has to be handed drug names.

Before this module the CLI forwarded only ``--limit``, so ``ingest --source all``
silently returned zero documents from ``openfda``, ``cms`` and ``nadac`` — an absence
of enquiry that reads like an absence of data. The profiles below close that.

Sourcing, so the seeding is auditable: ``lead_asset``/``indications`` follow the
per-company eval profiles in ``evals/epi/*.json`` and the Orphanet indication table;
``part_b_comparators`` is the table already maintained in ``memo.ingestion.asp``
(infused, physician-administered, so they appear in Medicare ASP); ``retail_comparators``
is its complement, the oral drugs that reach patients through the pharmacy benefit and
therefore appear in CMS Part D and NADAC rather than ASP. This is a hand-seeded table,
not a derived one: it is analyst input to the system and should be reviewed as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from memo.ingestion.asp import COMPARATORS as _ASP_COMPARATORS


@dataclass(frozen=True)
class CompanyProfile:
    """What a company is, in the vocabulary the public sources index on."""

    ticker: str
    name: str
    lead_asset: str
    #: Internal asset codes (KT-621, ABX464). Used for PubChem lookups and as
    #: retrieval keywords; most resolve to no public record, which is expected.
    asset_codes: Sequence[str] = ()
    #: Disease areas, as a source would name them.
    indications: Sequence[str] = ()
    #: Oral / retail comparators: CMS Part D and NADAC.
    retail_comparators: Sequence[str] = ()
    #: Free-text terms for PubMed and preprint search.
    literature_terms: Sequence[str] = ()

    @property
    def part_b_comparators(self) -> List[str]:
        """Infused comparators, from the table ``memo.ingestion.asp`` already owns."""
        return [str(d) for d in _ASP_COMPARATORS.get(self.ticker, ())]

    @property
    def all_comparators(self) -> List[str]:
        """Every comparator drug, infused and oral, de-duplicated in stable order."""
        seen: Dict[str, None] = {}
        for drug in list(self.part_b_comparators) + list(self.retail_comparators):
            seen.setdefault(drug, None)
        return list(seen)


PROFILES: Dict[str, CompanyProfile] = {
    "ABVX": CompanyProfile(
        ticker="ABVX",
        name="Abivax",
        lead_asset="obefazimod (ABX464)",
        asset_codes=("ABX464", "ABX196", "ABX203"),
        indications=("ulcerative colitis", "Crohn disease", "inflammatory bowel disease"),
        # Oral/self-injected UC therapies dispensed through the pharmacy benefit.
        retail_comparators=("tofacitinib", "upadacitinib", "ozanimod", "etrasimod",
                            "mesalamine", "adalimumab"),
        literature_terms=("obefazimod", "ABX464", "ulcerative colitis", "miR-124"),
    ),
    "KYMR": CompanyProfile(
        ticker="KYMR",
        name="Kymera Therapeutics",
        lead_asset="KT-621 (STAT6 degrader)",
        asset_codes=("KT-621", "KT-474", "KT-333", "KT-253", "KT-413", "KT-579"),
        indications=("atopic dermatitis", "hidradenitis suppurativa", "asthma"),
        retail_comparators=("dupilumab", "upadacitinib", "abrocitinib", "tralokinumab",
                            "lebrikizumab"),
        literature_terms=("STAT6 degrader", "IRAK4 degrader", "targeted protein degradation",
                          "atopic dermatitis"),
    ),
    "IMVT": CompanyProfile(
        ticker="IMVT",
        name="Immunovant",
        lead_asset="IMVT-1402 (anti-FcRn)",
        asset_codes=("IMVT-1401", "IMVT-1402"),
        indications=("Graves disease", "myasthenia gravis", "thyroid eye disease",
                     "chronic inflammatory demyelinating polyneuropathy"),
        retail_comparators=("methimazole", "prednisone", "mycophenolate", "azathioprine",
                            "pyridostigmine"),
        literature_terms=("FcRn", "batoclimab", "IMVT-1402", "Graves disease",
                          "myasthenia gravis"),
    ),
    "PRAX": CompanyProfile(
        ticker="PRAX",
        name="Praxis Precision Medicines",
        lead_asset="ulixacaltamide (essential tremor); vormatrigine (PRAX-628)",
        asset_codes=("PRAX-944", "PRAX-628", "PRAX-562", "PRAX-222", "PRAX-114",
                     "PRAX-100", "PRAX-090", "PRAX-080"),
        indications=("essential tremor", "focal epilepsy",
                     "developmental and epileptic encephalopathy"),
        # Praxis' comparators are oral CNS small molecules: Part D, never ASP.
        retail_comparators=("propranolol", "primidone", "topiramate", "levetiracetam",
                            "lamotrigine", "carbamazepine"),
        literature_terms=("essential tremor", "ulixacaltamide", "vormatrigine",
                          "focal onset seizure", "T-type calcium channel"),
    ),
    "COGT": CompanyProfile(
        ticker="COGT",
        name="Cogent Biosciences",
        lead_asset="bezuclastinib (KIT inhibitor)",
        asset_codes=("CGT9486", "CGT4859", "CGT6297", "CGT4255"),
        indications=("systemic mastocytosis", "gastrointestinal stromal tumor"),
        # On-target KIT TKI comparators, all oral.
        retail_comparators=("avapritinib", "midostaurin", "imatinib", "sunitinib",
                            "ripretinib"),
        literature_terms=("bezuclastinib", "KIT D816V", "systemic mastocytosis",
                          "gastrointestinal stromal tumor"),
    ),
}


def get_profile(ticker: str) -> CompanyProfile:
    """Return the profile for ``ticker``. Raises ``KeyError`` for an unknown ticker."""
    return PROFILES[ticker.strip().upper()]


def source_options(source: str, ticker: str) -> Dict[str, Any]:
    """Return the query options ``source`` needs for ``ticker``.

    Unknown tickers and sources that need nothing beyond the ticker both return an
    empty dict, so a caller can merge this unconditionally. The caller's own options
    take precedence — this only fills in what was not passed explicitly.
    """
    try:
        profile = get_profile(ticker)
    except KeyError:
        return {}

    if source == "openfda":
        # Labels and adverse events for the comparator landscape; the company's own
        # investigational assets are absent from openFDA by construction.
        return {"terms": profile.all_comparators,
                "indication": profile.indications[0] if profile.indications else ""}
    if source in ("cms", "nadac"):
        # Both index retail/pharmacy-benefit drugs, so oral comparators only.
        return {"drugs": list(profile.retail_comparators)}
    if source == "asp":
        # asp.py already owns this table; passing it keeps one source of truth.
        return {"drugs": profile.part_b_comparators}
    if source == "pubchem":
        return {"compounds": list(profile.asset_codes)}
    if source == "pubmed":
        return {"term": " OR ".join(f'"{t}"' for t in profile.literature_terms)}
    return {}
