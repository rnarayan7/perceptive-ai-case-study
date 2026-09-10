"""The coverage universe: the five companies and their lead-asset metadata.

Small and hand-maintained (spec §7.1). Companies in the ledger are bare tickers; this
adds the display metadata the dashboard needs. Extensible later.
"""

from __future__ import annotations

COMPANIES = {
    "ABVX": {"name": "Abivax", "lead_asset": "obefazimod",
             "indication": "Ulcerative colitis", "phase": "Ph3"},
    "KYMR": {"name": "Kymera Therapeutics", "lead_asset": "KT-621",
             "indication": "Atopic dermatitis", "phase": "Ph1b"},
    "PRAX": {"name": "Praxis Precision Medicines", "lead_asset": "ulixacaltamide",
             "indication": "Essential tremor", "phase": "NDA"},
    "IMVT": {"name": "Immunovant", "lead_asset": "IMVT-1402",
             "indication": "Graves' disease", "phase": "Ph3"},
    "COGT": {"name": "Cogent Biosciences", "lead_asset": "bezuclastinib",
             "indication": "Systemic mastocytosis", "phase": "Ph3"},
}

# Coverage order for the dashboard.
ORDER = ["ABVX", "KYMR", "PRAX", "IMVT", "COGT"]
