#!/usr/bin/env bash
# The whole thing, end to end, for one company:
#
#   scripts/run-all.sh KYMR
#
#   preflight -> ingest -> compose the memo -> evaluate
#
# This is the "executed as submitted from a fresh clone" path. Each step is also a
# script in its own right, so a failed run can be resumed at the step that failed
# rather than from the top — ingestion is the slow part and it is idempotent.

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TICKER="${1:-KYMR}"
require_company "$TICKER"
IGNORE_PREFLIGHT=0
[[ "${2:-}" == "--ignore-preflight" ]] && IGNORE_PREFLIGHT=1

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log "full run for $TICKER, started $started"
echo

# Stop on a failed preflight rather than pushing through it. With egress blocked
# every source still burns its full retry backoff before giving up, so a doomed run
# takes minutes to produce nothing; and a memo composed over an empty corpus is worse
# than no memo. --ignore-preflight forces the attempt anyway.
if ! "$here/doctor.sh"; then
  if (( IGNORE_PREFLIGHT )); then
    warn "preflight failed; continuing because --ignore-preflight was passed"
  else
    die "preflight failed — fix the above, or re-run with: $(basename "$0") $TICKER --ignore-preflight"
  fi
fi
echo

"$here/stage2-ingest.sh" "$TICKER"
echo
"$here/stage2-memo.sh" "$TICKER"
echo
"$here/stage2-eval.sh" "$TICKER"

echo
log "done: $TICKER, started $started, finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "artifacts: $SUBMISSION/stage2/$TICKER"
