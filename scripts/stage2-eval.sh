#!/usr/bin/env bash
# Stage 2: run the evaluation suite for one company.
#
#   scripts/stage2-eval.sh KYMR            # the deterministic evals (no model calls)
#   scripts/stage2-eval.sh KYMR --judged   # adds the model-graded evals (paid)
#
# Two tiers, deliberately separated. The deterministic evals — retrieval recall and
# structured extraction against gold — need no model and no key, so they can gate a
# commit. The judged evals put a model in the scoring loop, so they cost money and
# carry the judge's own error; the judge-probe below is what earns the right to
# read them, since it scores the judge on known-answer cases first.

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TICKER="${1:-}"
require_company "$TICKER"
shift

JUDGED=0
[[ "${1:-}" == "--judged" ]] && { JUDGED=1; shift; }

DATA_ROOT="${DATA_ROOT:-$REPO_ROOT/investment-memo/data}"
OUT="$SUBMISSION/stage2/$TICKER"
mkdir -p "$OUT"

ensure_venv
[[ -d "$DATA_ROOT/$TICKER" ]] || die "no corpus at $DATA_ROOT/$TICKER — run scripts/stage2-ingest.sh $TICKER first"

cd "$REPO_ROOT/investment-memo"
run_eval() {
  local kind="$1"; shift
  log "eval: $kind"
  # An eval that fails is a result, not a crash: keep going and let the log carry it.
  "$PY" -m memo.cli eval --type "$kind" --company "$TICKER" --data-root "$DATA_ROOT" "$@" || \
    warn "eval '$kind' exited non-zero — see the log"
  echo
}

{
  log "deterministic evals (no model in the scorer)"
  run_eval retrieval
  run_eval epi-retrieval

  if (( JUDGED )); then
    require_key
    log "model-graded evals"
    # Validate the judge before trusting anything it grades.
    log "eval: judge-probe (known-answer cases for the faithfulness judge)"
    "$PY" -m memo.cli eval --type judge-probe || warn "judge-probe exited non-zero"
    echo
    run_eval faithfulness
    run_eval memo
    run_eval known-bad
  else
    log "skipping model-graded evals (pass --judged to include them)"
  fi
} 2>&1 | tee "$OUT/eval-run.log"

echo
log "eval log: $OUT/eval-run.log"
[[ -d "$REPO_ROOT/investment-memo/evals/results" ]] && \
  log "saved eval results: investment-memo/evals/results/"
