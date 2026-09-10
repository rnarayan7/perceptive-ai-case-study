#!/usr/bin/env bash
# Stage 1: score the figure extractor against the gold set, and report the cost.
#
#   scripts/stage1-eval.sh                    # combined extractor, verified gold only
#   scripts/stage1-eval.sh --extractor vlm    # VLM read alone, no CV cross-check
#   scripts/stage1-eval.sh --extractor stub   # no model calls, no key needed
#
# The six brief figures and the gold set are committed, so this needs no ingestion
# and no network beyond the model API. The extractor package is standard-library
# only by design, so it runs on the system python with nothing installed.
#
# Default is --extractor combined: the VLM read plus the independent CV pixel
# measurement. That pairing is the point of the design — disagreement between two
# genuinely independent reads is the triage signal — so it is what should be scored.

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

EXTRACTOR="combined"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --extractor) EXTRACTOR="$2"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done

[[ "$EXTRACTOR" == "stub" ]] || require_key

OUT="$SUBMISSION/stage1"
mkdir -p "$OUT"

cd "$REPO_ROOT/figure-extraction"
log "scoring the $EXTRACTOR extractor over the verified gold set"

start=$SECONDS
python3 -m evaluation.run \
  --extractor "$EXTRACTOR" \
  --verified-only \
  --samples 1 \
  --out "$OUT/eval_report.json" \
  "${args[@]}" 2>&1 | tee "$OUT/eval-run.log"
elapsed=$((SECONDS - start))

echo
log "scored in ${elapsed}s"
log "report: $OUT/eval_report.json"

# The cost line is a deliverable in its own right ("the approximate cost of one run"),
# so surface it rather than leaving it in the scrollback.
if grep -q 'Cost of this run' "$OUT/eval-run.log"; then
  grep 'Cost of this run' "$OUT/eval-run.log"
else
  warn "no cost line — the stub extractor makes no model calls, so there is nothing to cost"
fi
