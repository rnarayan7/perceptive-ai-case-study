"""Seed a sample ledger + matching rendered-memo artifact for development and tests.

This stands in for the composition engine (built in parallel) so the app can be developed
and tested end to end. It writes:

* a memo + a handful of claims/evidence into a ``LedgerStore`` (the audit source of truth),
* a matching ``<memos_dir>/<memo_id>.json`` rendered-memo artifact whose citation markers
  point at the very ``evidence_id``s just written — so auditing a marker resolves.

Run standalone to populate the defaults for manual poking::

    python -m app.seed          # -> data/ledger.db + data/memos/<memo_id>.json

or import :func:`seed` and pass paths (what the tests do).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from memo.ledger import ClaimRecord, EvidenceRecord, LedgerStore, MemoRecord

SAMPLE_MEMO_ID = "KYMR-sample-run"
SAMPLE_COMPANY = "KYMR"


def seed(db_path: str, memos_dir: str, memo_id: str = SAMPLE_MEMO_ID) -> str:
    """Write the sample memo to the ledger and its rendered artifact to ``memos_dir``.

    Returns the ``memo_id`` so callers can build URLs / assertions against it.
    """
    store = LedgerStore(db_path=db_path)
    try:
        memo, claims = _build_records(memo_id)
        store.write_memo(memo, claims)
        artifact = _build_artifact(memo, claims)
    finally:
        store.close()

    Path(memos_dir).mkdir(parents=True, exist_ok=True)
    out = Path(memos_dir) / f"{memo_id}.json"
    with out.open("w") as fh:
        json.dump(artifact, fh, indent=2)
    return memo_id


def _build_records(memo_id: str):
    """The ledger side: one memo, a few claims, each with evidence (stable ids)."""
    memo = MemoRecord(
        memo_id=memo_id,
        company=SAMPLE_COMPANY,
        status="complete",
        recommendation="Buy",
        thesis=(
            "The market is pricing KT-474 as a typical mid-stage asset, but the IRAK4 "
            "degrader mechanism and clean early PD support a higher probability of "
            "success than the current price implies."
        ),
    )

    moa_claim = ClaimRecord(
        memo_id=memo_id, section="moa", module="moa",
        statement="KT-474 is an orally available, selective IRAK4 degrader.",
        confidence=0.82, rationale="Mechanism confirmed by clinical PD readouts.",
        evidence=[EvidenceRecord(
            doc_id="NCT04772885", source="ClinicalTrials.gov", doc_type="trial_record",
            url="https://clinicaltrials.gov/study/NCT04772885",
            quote="KT-474 is an oral small-molecule degrader of IRAK4.",
            date="2023-06-01",
        )],
    )
    pos_claim = ClaimRecord(
        memo_id=memo_id, section="pos", module="pos",
        statement="Phase 1 showed >90% IRAK4 knockdown with a favorable safety profile.",
        confidence=0.7, rationale="Dose-dependent target engagement in healthy volunteers.",
        value="0.42",
        evidence=[EvidenceRecord(
            doc_id="KYMR-10Q-2023Q2", source="SEC EDGAR", doc_type="10-Q",
            url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=KYMR",
            quote="mean IRAK4 knockdown exceeded 90% at the top dose with no treatment-related SAEs.",
            date="2023-08-08",
        )],
    )
    val_claim = ClaimRecord(
        memo_id=memo_id, section="valuation", module="price",
        statement="rNPV implies roughly 60% upside to the current share price.",
        confidence=0.6, rationale="Risk-adjusted peak sales discounted at 12%.",
        value="$58",
        evidence=[EvidenceRecord(
            doc_id="KYMR-10Q-2023Q2", source="SEC EDGAR", doc_type="10-Q",
            url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=KYMR",
            quote="cash, cash equivalents and marketable securities of $612.3 million.",
            date="2023-08-08",
        )],
    )
    return memo, [moa_claim, pos_claim, val_claim]


def _cite(claim: ClaimRecord, marker: str, label: str) -> dict:
    """A rendered-memo citation pointing at the claim's first evidence row."""
    ev = claim.evidence[0]
    return {"marker": marker, "evidence_id": ev.evidence_id, "url": ev.url, "label": label}


def _build_artifact(memo: MemoRecord, claims) -> dict:
    """The rendered-memo side: prose with inline markers matching the citations above."""
    moa, pos, val = claims
    return {
        "memo_id": memo.memo_id,
        "company": memo.company,
        "title": "KYMR — Investment Memo",
        "recommendation": memo.recommendation,
        "thesis": memo.thesis,
        "sections": [
            {
                "section_id": "thesis", "title": "Executive Summary & Thesis",
                "prose": memo.thesis,
                "citations": [], "figures": [],
            },
            {
                "section_id": "moa", "title": "Mechanism of Action",
                "prose": (
                    "KT-474 is an orally available, selective IRAK4 degrader [1]. By "
                    "removing the IRAK4 scaffold rather than inhibiting its kinase "
                    "activity, it blunts both signaling arms downstream of the receptor."
                ),
                "citations": [_cite(moa, "[1]", "ClinicalTrials.gov NCT04772885")],
                "figures": [],
            },
            {
                "section_id": "pos", "title": "Probability of Success",
                "prose": (
                    "The Phase 1 program showed greater than 90% IRAK4 knockdown with a "
                    "favorable safety profile [1], which lifts our evidence-grounded PoS "
                    "above the generic phase-transition base rate for the indication."
                ),
                "citations": [_cite(pos, "[1]", "KYMR 10-Q Q2 2023")],
                "figures": [{
                    "figure_id": "fig_pos_knockdown",
                    "caption": "Dose-dependent IRAK4 knockdown, Phase 1.",
                    "image_ref": "/static/figures/kt474_knockdown.png",
                    "claim_id": pos.claim_id,
                }],
            },
            {
                "section_id": "valuation", "title": "Valuation & Price vs Thesis",
                "prose": (
                    "Our rNPV implies roughly 60% upside to the current share price [1]. "
                    "A cash runway of $612 million funds the program past the next major "
                    "readout, so the balance sheet does not force a raise into weakness."
                ),
                "citations": [_cite(val, "[1]", "KYMR 10-Q Q2 2023")],
                "figures": [],
            },
        ],
    }


if __name__ == "__main__":
    from app.server import DEFAULT_DB_PATH, DEFAULT_MEMOS_DIR

    mid = seed(DEFAULT_DB_PATH, DEFAULT_MEMOS_DIR)
    print(f"seeded memo {mid} -> {DEFAULT_DB_PATH} + {DEFAULT_MEMOS_DIR}/{mid}.json")
