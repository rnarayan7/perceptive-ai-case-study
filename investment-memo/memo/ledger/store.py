"""SQLite-backed ledger store.

Stdlib ``sqlite3`` only, one file, so a fresh clone needs no infrastructure. Stable ids
make a claim deep-linkable to its evidence (what the web app needs). Writing a memo is a
single transaction so a run is all-or-nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from memo.ledger.schema import (
    ClaimRecord,
    EvidenceRecord,
    FeedbackMessageRecord,
    FeedbackSessionRecord,
    GenerationRunRecord,
    MemoRecord,
    SectionRecord,
)

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
    rationale TEXT,
    value_num REAL,
    value_unit TEXT
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
CREATE TABLE IF NOT EXISTS memo_sections (
    memo_id TEXT NOT NULL REFERENCES memos(memo_id),
    section_id TEXT NOT NULL,
    module TEXT,
    confidence REAL,
    tier TEXT,
    summary TEXT,
    PRIMARY KEY (memo_id, section_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_memo ON claims(memo_id);
CREATE INDEX IF NOT EXISTS idx_claims_section ON claims(memo_id, section);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);
CREATE TABLE IF NOT EXISTS generation_runs (
    run_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger TEXT,
    output TEXT,
    refusal_reason TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    duration_s REAL,
    ran_at TEXT NOT NULL,
    memo_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sections_memo ON memo_sections(memo_id);
CREATE INDEX IF NOT EXISTS idx_genruns_ran_at ON generation_runs(ran_at);
CREATE TABLE IF NOT EXISTS feedback_sessions (
    session_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_label TEXT NOT NULL,
    highlighted_quote TEXT,
    status TEXT NOT NULL,
    issue TEXT,
    severity TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES feedback_sessions(session_id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fbsess_company ON feedback_sessions(company);
CREATE INDEX IF NOT EXISTS idx_fbmsg_session ON feedback_messages(session_id, message_id);
"""


class LedgerStore:
    """Read/write access to persisted memos, claims, and evidence."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        # Wait (up to 5s) for a lock rather than erroring, so a read during a concurrent
        # compose write (e.g. the app serving while the worker regenerates) doesn't 500.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------- write

    def write_memo(
        self,
        memo: MemoRecord,
        claims: List[ClaimRecord],
        sections: Optional[List[SectionRecord]] = None,
    ) -> str:
        """Persist a memo, its claims + evidence, and per-section rollups in one txn."""
        with self._conn:  # transaction: commit on success, rollback on error
            self._conn.execute(
                "INSERT OR REPLACE INTO memos VALUES (?,?,?,?,?,?,?)",
                (memo.memo_id, memo.company, memo.status, memo.recommendation,
                 memo.thesis, memo.notes, memo.created_at),
            )
            # A re-run of the same memo_id replaces its rows; clear the child rows first
            # (their ids are freshly generated, so INSERT OR REPLACE alone would orphan).
            self._conn.execute("DELETE FROM memo_sections WHERE memo_id = ?", (memo.memo_id,))
            for claim in claims:
                self._conn.execute(
                    "INSERT OR REPLACE INTO claims "
                    "(claim_id, memo_id, section, module, statement, value, confidence, "
                    "rationale, value_num, value_unit) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (claim.claim_id, memo.memo_id, claim.section, claim.module,
                     claim.statement, claim.value, claim.confidence, claim.rationale,
                     claim.value_num, claim.value_unit),
                )
                for ev in claim.evidence:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?,?,?,?,?)",
                        (ev.evidence_id, claim.claim_id, ev.doc_id, ev.source,
                         ev.doc_type, ev.url, ev.quote, ev.date, ev.chunk_id),
                    )
            for section in sections or []:
                self._conn.execute(
                    "INSERT OR REPLACE INTO memo_sections VALUES (?,?,?,?,?,?)",
                    (memo.memo_id, section.section_id, section.module,
                     section.confidence, section.tier, section.summary),
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

    def write_generation_run(self, run: GenerationRunRecord) -> str:
        """Persist a run-level summary of one memo generation (Activity feed)."""
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO generation_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run.run_id, run.company, run.status, run.trigger, run.output,
                 run.refusal_reason, run.model, run.input_tokens, run.output_tokens,
                 run.cost_usd, run.duration_s, run.ran_at, run.memo_id),
            )
        return run.run_id

    def list_generation_runs(self, limit: Optional[int] = None) -> List[GenerationRunRecord]:
        sql = "SELECT * FROM generation_runs ORDER BY ran_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [self._genrun_from_row(r) for r in self._conn.execute(sql).fetchall()]

    def write_feedback_session(self, session: FeedbackSessionRecord) -> str:
        """Upsert a feedback session's row (metadata + issue/severity/status/summary).

        Idempotent: the chat service calls this on session create and again after each
        turn to persist the agent's updated summary. Message turns are written separately
        via ``append_feedback_message`` so re-upserting the row never churns them.
        """
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO feedback_sessions "
                "(session_id, company, section_id, section_label, highlighted_quote, "
                "status, issue, severity, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session.session_id, session.company, session.section_id,
                 session.section_label, session.highlighted_quote, session.status,
                 session.issue, session.severity, session.created_at, session.updated_at),
            )
        return session.session_id

    def append_feedback_message(
        self, session_id: str, message: FeedbackMessageRecord
    ) -> None:
        """Append one turn (analyst or agent) to a feedback session."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO feedback_messages (session_id, role, text, ts) VALUES (?,?,?,?)",
                (session_id, message.role, message.text, message.ts),
            )

    def get_feedback_session(self, session_id: str) -> Optional[FeedbackSessionRecord]:
        """One feedback session with its message turns, oldest first."""
        row = self._conn.execute(
            "SELECT * FROM feedback_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        session = self._feedback_from_row(row)
        session.messages = self._feedback_messages(session_id)
        return session

    def list_feedback_sessions(
        self, company: Optional[str] = None
    ) -> List[FeedbackSessionRecord]:
        """Feedback sessions (message turns not loaded), most recently updated first."""
        if company:
            rows = self._conn.execute(
                "SELECT * FROM feedback_sessions WHERE company = ? ORDER BY updated_at DESC",
                (company,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM feedback_sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [self._feedback_from_row(r) for r in rows]

    def _feedback_messages(self, session_id: str) -> List[FeedbackMessageRecord]:
        rows = self._conn.execute(
            "SELECT role, text, ts FROM feedback_messages WHERE session_id = ? "
            "ORDER BY message_id",
            (session_id,),
        ).fetchall()
        return [
            FeedbackMessageRecord(role=r["role"], text=r["text"], ts=r["ts"]) for r in rows
        ]

    def get_sections(self, memo_id: str) -> List[SectionRecord]:
        """Per-section rollups (confidence, tier, summary) for a memo."""
        rows = self._conn.execute(
            "SELECT * FROM memo_sections WHERE memo_id = ?", (memo_id,)
        ).fetchall()
        return [
            SectionRecord(
                memo_id=r["memo_id"], section_id=r["section_id"], module=r["module"],
                confidence=r["confidence"], tier=r["tier"] or "", summary=r["summary"] or "",
            )
            for r in rows
        ]

    def get_claim(self, claim_id: str) -> Optional[ClaimRecord]:
        row = self._conn.execute(
            "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            return None
        claim = self._claim_from_row(row)
        claim.evidence = self.get_evidence(claim.claim_id)
        return claim

    def get_evidence_by_id(self, evidence_id: str) -> Optional[EvidenceRecord]:
        """Resolve one evidence row by its own id (the audit-drawer reverse lookup)."""
        row = self._conn.execute(
            "SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        return self._evidence_from_row(row) if row else None

    def get_claim_for_evidence(self, evidence_id: str) -> Optional[ClaimRecord]:
        """The claim an evidence row backs, joined evidence->claims in one lookup."""
        row = self._conn.execute(
            "SELECT c.* FROM claims c JOIN evidence e ON e.claim_id = c.claim_id "
            "WHERE e.evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            return None
        claim = self._claim_from_row(row)
        claim.evidence = self.get_evidence(claim.claim_id)
        return claim

    def count_evidence_by_doc(self, doc_id: str) -> int:
        """How many evidence rows cite a given document (the Data library cited-by count)."""
        row = self._conn.execute(
            "SELECT count(*) AS n FROM evidence WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return int(row["n"]) if row else 0

    def cited_by(self, doc_id: str) -> List[dict]:
        """The memos/sections that cite a document, joined evidence->claims->memos.

        One row per (memo, section) the document backs, so the document detail page can
        list where a source was used. Ordered by memo recency.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT m.memo_id AS memo_id, m.company AS company, c.section AS section, "
            "m.created_at AS created_at "
            "FROM evidence e "
            "JOIN claims c ON e.claim_id = c.claim_id "
            "JOIN memos m ON c.memo_id = m.memo_id "
            "WHERE e.doc_id = ? "
            "ORDER BY m.created_at DESC, c.section",
            (doc_id,),
        ).fetchall()
        return [
            {"memo_id": r["memo_id"], "company": r["company"], "section": r["section"]}
            for r in rows
        ]

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
            value_num=row["value_num"], value_unit=row["value_unit"],
        )

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=row["evidence_id"], doc_id=row["doc_id"], source=row["source"],
            doc_type=row["doc_type"], url=row["url"], quote=row["quote"],
            date=row["date"], chunk_id=row["chunk_id"],
        )

    @staticmethod
    def _feedback_from_row(row: sqlite3.Row) -> FeedbackSessionRecord:
        return FeedbackSessionRecord(
            session_id=row["session_id"], company=row["company"],
            section_id=row["section_id"], section_label=row["section_label"],
            highlighted_quote=row["highlighted_quote"], status=row["status"],
            issue=row["issue"], severity=row["severity"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _genrun_from_row(row: sqlite3.Row) -> GenerationRunRecord:
        return GenerationRunRecord(
            run_id=row["run_id"], company=row["company"], status=row["status"],
            trigger=row["trigger"] or "manual", output=row["output"] or "",
            refusal_reason=row["refusal_reason"], model=row["model"] or "",
            input_tokens=row["input_tokens"] or 0, output_tokens=row["output_tokens"] or 0,
            cost_usd=row["cost_usd"] or 0.0, duration_s=row["duration_s"] or 0.0,
            ran_at=row["ran_at"], memo_id=row["memo_id"],
        )
