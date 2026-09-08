"""Audit web app: a read-only surface over produced memos.

Two data sources, kept distinct:

* the **ledger** (SQLite via ``memo.ledger.LedgerStore``) is the audit source of truth
  for the memo index and for every claim's evidence. Nothing here writes to it.
* the **rendered-memo artifact** (JSON at ``<memos_dir>/<memo_id>.json``) is what the
  composition engine produces: the prose, inline citation markers, and figures we show.

The app never invents prose or evidence; it renders what those two sources hold and lets
a reader walk any citation marker back to the ledger evidence it points at.
"""

from app.server import create_app

__all__ = ["create_app"]
