"""Run the audit app: ``python -m app`` (serves on http://127.0.0.1:5000).

Reads the ledger at ``data/ledger.db`` and artifacts at ``data/memos/``. Seed a sample
first with ``python -m app.seed`` if the ledger is empty.
"""

from app.server import create_app

if __name__ == "__main__":
    create_app().run(debug=True)
