#!/usr/bin/env bash
# Start the read API (FastAPI over the memo ledger) on 127.0.0.1:8001.
# Reads the committed data snapshot under investment-memo/data (ledger, memos, figures).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="backend:investment-memo"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8001}" --log-level warning
