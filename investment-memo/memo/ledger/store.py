"""SQLite-backed ledger store.

Stdlib ``sqlite3`` only, one file, so a fresh clone needs no infrastructure. Stable ids
make a claim deep-linkable to its evidence (what the web app needs). Writing a memo is a
single transaction so a run is all-or-nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from memo.ledger.schema import ClaimRecord, EvidenceRecord, MemoRecord

DEFAULT_DB_PATH = "data/ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memos (
    memo_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    status TEXT NOT NULL,
    recommendation TEXT,
    thesis TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    memo_id TEXT NOT NULL REFERENCES memos(memo_id),
    section TEXT NOT NULL,
    module TEXT NOT NULL,
    statement TEXT NOT NULL,
    value TEXT,
    confidence REAL NOT NULL,
    rationale TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    doc_id TEXT,
    source TEXT,
    doc_type TEXT,
    url TEXT,
    quote TEXT,
    date TEXT,
    chunk_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_memo ON claims(memo_id);
CREATE INDEX IF NOT EXISTS idx_claims_section ON claims(memo_id, section);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);
"""


class LedgerStore:
    """Read/write access to persisted memos, claims, and evidence."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------- write

    def write_memo(self, memo: MemoRecord, claims: List[ClaimRecord]) -> str:
        """Persist a memo and all its claims + evidence in one transaction."""
        with self._conn:  # transaction: commit on success, rollback on error
            self._conn.execute(
                "INSERT OR REPLACE INTO memos VALUES (?,?,?,?,?,?,?)",
                (memo.memo_id, memo.company, memo.status, memo.recommendation,
                 memo.thesis, memo.notes, memo.created_at),
            )
            for claim in claims:
                self._conn.execute(
                    "INSERT OR REPLACE INTO claims VALUES (?,?,?,?,?,?,?,?)",
                    (claim.claim_id, memo.memo_id, claim.section, claim.module,
                     claim.statement, claim.value, claim.confidence, claim.rationale),
                )
                for ev in claim.evidence:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?,?,?,?,?)",
                        (ev.evidence_id, claim.claim_id, ev.doc_id, ev.source,
                         ev.doc_type, ev.url, ev.quote, ev.date, ev.chunk_id),
                    )
        return memo.memo_id

    # -------------------------------------------------------------------- read

    def get_memo(self, memo_id: str) -> Optional[MemoRecord]:
        row = self._conn.execute(
            "SELECT * FROM memos WHERE memo_id = ?", (memo_id,)
        ).fetchone()
        return self._memo_from_row(row) if row else None

    def latest_memo(self, company: str) -> Optional[MemoRecord]:
        row = self._conn.execute(
            "SELECT * FROM memos WHERE company = ? ORDER BY created_at DESC LIMIT 1",
            (company,),
        ).fetchone()
        return self._memo_from_row(row) if row else None

    def list_memos(self, company: Optional[str] = None) -> List[MemoRecord]:
        if company:
            rows = self._conn.execute(
                "SELECT * FROM memos WHERE company = ? ORDER BY created_at DESC", (company,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memos ORDER BY created_at DESC"
            ).fetchall()
        return [self._memo_from_row(r) for r in rows]

    def get_claims(self, memo_id: str, section: Optional[str] = None) -> List[ClaimRecord]:
        if section is not None:
            rows = self._conn.execute(
                "SELECT * FROM claims WHERE memo_id = ? AND section = ?", (memo_id, section)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM claims WHERE memo_id = ?", (memo_id,)
            ).fetchall()
        claims = [self._claim_from_row(r) for r in rows]
        for claim in claims:
            claim.evidence = self.get_evidence(claim.claim_id)
        return claims

    def get_evidence(self, claim_id: str) -> List[EvidenceRecord]:
        rows = self._conn.execute(
            "SELECT * FROM evidence WHERE claim_id = ?", (claim_id,)
        ).fetchall()
        return [self._evidence_from_row(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    # --------------------------------------------------------------- internals

    @staticmethod
    def _memo_from_row(row: sqlite3.Row) -> MemoRecord:
        return MemoRecord(
            memo_id=row["memo_id"], company=row["company"], status=row["status"],
            recommendation=row["recommendation"], thesis=row["thesis"],
            notes=row["notes"] or "", created_at=row["created_at"],
        )

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> ClaimRecord:
        return ClaimRecord(
            claim_id=row["claim_id"], memo_id=row["memo_id"], section=row["section"],
            module=row["module"], statement=row["statement"], value=row["value"],
            confidence=row["confidence"], rationale=row["rationale"] or "",
        )

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=row["evidence_id"], doc_id=row["doc_id"], source=row["source"],
            doc_type=row["doc_type"], url=row["url"], quote=row["quote"],
            date=row["date"], chunk_id=row["chunk_id"],
        )
