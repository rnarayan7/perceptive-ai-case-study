#!/usr/bin/env bash
# Generate a fresh investment memo for one company. Makes real, paid model calls.
# Usage: scripts/memo.sh <TICKER> [model]   e.g. scripts/memo.sh ABVX
# Requires ANTHROPIC_API_KEY (in investment-memo/.env or the environment).
set -euo pipefail
company="${1:?usage: scripts/memo.sh <TICKER> [model]  (e.g. ABVX KYMR PRAX IMVT COGT)}"
model="${2:-claude-sonnet-5}"
cd "$(dirname "$0")/../investment-memo"
exec python3 -m memo.cli compose --company "$company" --model "$model"
