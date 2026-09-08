"""The ledger: persisted memo output (claims + evidence).

The durable provenance of a memo: every claim and the evidence it rests on, keyed by a
``memo_id`` that is also the run's ``run_id`` (so it correlates with the execution trace).
Both the memo renderer and the web app read from here; it is the single source of truth
the memo is a view of. Execution telemetry lives separately in :mod:`memo.trace`.
"""

from memo.ledger.schema import ClaimRecord, EvidenceRecord, MemoRecord
from memo.ledger.store import LedgerStore

__all__ = ["MemoRecord", "ClaimRecord", "EvidenceRecord", "LedgerStore"]
