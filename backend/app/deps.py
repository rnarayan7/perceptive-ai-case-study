"""Shared dependencies: where the data lives, and per-request store access.

v1 reads the existing SQLite ledger + rendered-memo JSON that the memo engine writes
(spec "Lean v1 scope"). The data root defaults to the sibling investment-memo/data dir;
override with MEMO_DATA_ROOT (e.g. the Render disk path).
"""

from __future__ import annotations

import os
from pathlib import Path

from memo.ingestion.base import Storage
from memo.ledger import LedgerStore

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "investment-memo" / "data"
DATA_ROOT = Path(os.environ.get("MEMO_DATA_ROOT", str(_DEFAULT_ROOT)))
LEDGER_DB = str(DATA_ROOT / "ledger.db")


def get_ledger():
    """A short-lived ledger connection per request (closed after)."""
    store = LedgerStore(db_path=LEDGER_DB)
    try:
        yield store
    finally:
        store.close()


def get_storage() -> Storage:
    """Storage rooted at the data dir, for reading rendered-memo artifacts + figures."""
    return Storage(root=DATA_ROOT)
