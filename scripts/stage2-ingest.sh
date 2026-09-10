#!/usr/bin/env bash
# Stage 2, step 1: build the document corpus for one company.
#
#   scripts/stage2-ingest.sh KYMR [--limit 25] [--force]
#
# Runs every registered source through memo.cli, passing each the query terms it
# needs from memo.companies (comparator drugs, asset codes, literature terms).
# Idempotent: unchanged documents are skipped by content hash, so re-running is
# cheap and safe. Makes no model calls, so it needs no API key.

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TICKER="${1:-}"
require_company "$TICKER"
shift

DATA_ROOT="${DATA_ROOT:-$REPO_ROOT/investment-memo/data}"

ensure_venv
log "ingesting $TICKER into $DATA_ROOT"
log "sources: every key in memo.ingestion.REGISTRY, with per-source query terms"

cd "$REPO_ROOT/investment-memo"
start=$SECONDS

# --source all walks the registry; the CLI merges in each source's profile options.
# Exit code 1 means at least one source reported an error, which is common and not
# fatal (a rate limit, or a company with no record in that source), so the summary
# is what to read, not the status.
set +e
"$PY" -m memo.cli ingest --source all --company "$TICKER" --data-root "$DATA_ROOT" "$@"
status=$?
set -e

elapsed=$((SECONDS - start))
echo
log "ingest finished in ${elapsed}s (exit $status)"

if [[ -d "$DATA_ROOT/$TICKER" ]]; then
  log "corpus by source:"
  for dir in "$DATA_ROOT/$TICKER"/*/; do
    [[ -d "$dir" ]] || continue
    printf '  %-18s %s documents\n' "$(basename "$dir")" "$(find "$dir" -type f | wc -l | tr -d ' ')"
  done
else
  warn "no corpus directory at $DATA_ROOT/$TICKER — every source returned empty."
  warn "run scripts/doctor.sh; blocked egress is the usual cause."
fi
