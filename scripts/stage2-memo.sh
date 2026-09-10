#!/usr/bin/env bash
# Stage 2, step 2: compose the investment memorandum for one company.
#
#   scripts/stage2-memo.sh KYMR [--model claude-opus-5]
#
# One run, no human in the loop: the compose engine runs the five analysis modules,
# writes claims and evidence to the ledger, synthesises the thesis, annotates the
# figures, and renders the memo. Then this script collects everything the brief asks
# to see into submission/stage2/ — the memo, the annotated figures, the execution
# trace, and the run's cost.
#
# This makes paid model calls.

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TICKER="${1:-}"
require_company "$TICKER"
shift

DATA_ROOT="${DATA_ROOT:-$REPO_ROOT/investment-memo/data}"
OUT="$SUBMISSION/stage2/$TICKER"

ensure_venv
require_key

[[ -d "$DATA_ROOT/$TICKER" ]] || die "no corpus at $DATA_ROOT/$TICKER — run scripts/stage2-ingest.sh $TICKER first"

mkdir -p "$OUT/figures"
log "composing the $TICKER memo (this makes paid model calls)"

cd "$REPO_ROOT/investment-memo"
start=$SECONDS

# Tee so the run log is kept verbatim: it carries the cost summary and any refusal
# note, both of which the write-up has to quote rather than paraphrase.
"$PY" -m memo.cli compose --company "$TICKER" --data-root "$DATA_ROOT" --markdown "$@" \
  2>&1 | tee "$OUT/compose-run.log"

elapsed=$((SECONDS - start))

memo_id="$(grep -m1 '^memo_id:' "$OUT/compose-run.log" | awk '{print $2}')"
[[ -n "$memo_id" ]] || die "compose did not report a memo_id — see $OUT/compose-run.log"

status="$(grep -m1 '^status:' "$OUT/compose-run.log" | awk '{print $2}')"
log "memo $memo_id finished in ${elapsed}s with status: ${status:-unknown}"

# The rendered memo, its trace, and the annotated images. Copied rather than moved:
# data/ stays the working corpus, submission/ is the artifact set that gets committed.
cp "$DATA_ROOT/memos/$memo_id.json" "$OUT/memo.json"
[[ -f "$DATA_ROOT/traces/$memo_id.jsonl" ]] && cp "$DATA_ROOT/traces/$memo_id.jsonl" "$OUT/trace.jsonl"

# Markdown: rendered from the ledger by memo_id rather than scraped out of the run log.
# The log interleaves the cost summary and any refusal note with the memo text, so
# parsing it would fold those into the document.
"$PY" - "$memo_id" "$DATA_ROOT" <<'PY' > "$OUT/memo.md"
import sys
from memo.compose import render_markdown_for
from memo.ingestion.base import Storage

memo_id, data_root = sys.argv[1], sys.argv[2]
sys.stdout.write(render_markdown_for(memo_id, Storage(root=data_root)))
PY

annotated="$DATA_ROOT/$TICKER/figures/annotated"
if [[ -d "$annotated" ]] && compgen -G "$annotated/*.png" > /dev/null; then
  cp "$annotated"/*.png "$OUT/figures/"
  log "collected $(find "$OUT/figures" -name '*.png' | wc -l | tr -d ' ') annotated figure(s)"
else
  warn "no annotated figures — check the figure manifest for $TICKER"
fi

echo
log "artifacts in $OUT"
printf '  memo.json        the rendered memo (claims, sections, citations)\n'
printf '  memo.md          the memo as prose\n'
printf '  trace.jsonl      the execution trace (the inspectable reasoning path)\n'
printf '  figures/         the figures as the system annotated them\n'
printf '  compose-run.log  the full run log, including the cost of this run\n'
echo
grep -iE 'cost|tokens' "$OUT/compose-run.log" | tail -3 || true
