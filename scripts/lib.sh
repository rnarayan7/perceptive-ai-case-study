#!/usr/bin/env bash
# Shared helpers for the run scripts. Sourced, not executed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$REPO_ROOT/.venv}"
PY="$VENV/bin/python"
SUBMISSION="${SUBMISSION:-$REPO_ROOT/submission}"

# The five companies the brief names.
COMPANIES=(ABVX KYMR PRAX IMVT COGT)

log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Create the venv and install the engine if it is not already there. Idempotent:
# a second call is a no-op, so every script can call it without a guard.
ensure_venv() {
  if [[ ! -x "$PY" ]]; then
    log "creating venv at $VENV"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
  fi
  if ! "$PY" -c 'import memo' 2>/dev/null; then
    log "installing the memo engine (editable)"
    "$VENV/bin/pip" install --quiet -e "$REPO_ROOT/investment-memo"
  fi
}

require_key() {
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    # The engine also reads a local .env; honour the same file here so the two
    # paths agree about where credentials come from.
    if [[ -f "$REPO_ROOT/.env" ]]; then
      set +u
      # shellcheck disable=SC1091
      source <(grep -E '^\s*(export\s+)?ANTHROPIC_API_KEY=' "$REPO_ROOT/.env" | sed 's/^\s*export\s*//; s/^/export /')
      set -u
    fi
  fi
  [[ -n "${ANTHROPIC_API_KEY:-}" ]] || die "ANTHROPIC_API_KEY is not set (export it, or put it in $REPO_ROOT/.env)"
}

# Confirm a ticker is one the system knows, so a typo fails before any network call.
require_company() {
  local ticker="${1:-}"
  [[ -n "$ticker" ]] || die "usage: $(basename "$0") <TICKER>  (one of: ${COMPANIES[*]})"
  local c
  for c in "${COMPANIES[@]}"; do
    [[ "$c" == "$ticker" ]] && return 0
  done
  die "unknown ticker '$ticker' (expected one of: ${COMPANIES[*]})"
}
