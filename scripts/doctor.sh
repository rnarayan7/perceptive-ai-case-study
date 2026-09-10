#!/usr/bin/env bash
# Preflight: can this machine actually run the system?
#
# The brief says the repository is executed from a fresh clone, so the first thing
# it should be able to answer is what is missing. Checks the interpreter, the venv,
# credentials, and reachability of every host the ingestion sources call. Exits
# non-zero if anything required is missing, so CI can gate on it.

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

failures=0
note_fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; failures=$((failures + 1)); }
note_ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
note_warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }

log "interpreter"
py_version="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  note_ok "python $py_version (>= 3.9 required)"
else
  note_fail "python $py_version is below the required 3.9"
fi

log "environment"
if [[ -x "$PY" ]]; then
  note_ok "venv present at $VENV"
else
  note_warn "no venv at $VENV (the run scripts create one on first use)"
fi
avail_kb="$(df -Pk "$REPO_ROOT" | awk 'NR==2 {print $4}')"
if (( avail_kb > 2 * 1024 * 1024 )); then
  note_ok "disk: $((avail_kb / 1024 / 1024)) GB free"
else
  note_warn "disk: only $((avail_kb / 1024)) MB free; a full five-company corpus wants a few GB"
fi

log "credentials"
key="${ANTHROPIC_API_KEY:-}"
if [[ -z "$key" && -f "$REPO_ROOT/.env" ]] && grep -qE '^\s*(export\s+)?ANTHROPIC_API_KEY=' "$REPO_ROOT/.env"; then
  key="from-dotenv"
  note_ok "ANTHROPIC_API_KEY found in .env"
elif [[ -n "$key" ]]; then
  note_ok "ANTHROPIC_API_KEY set in the environment"
else
  note_fail "ANTHROPIC_API_KEY is not set — analysis, compose and the Stage 1 VLM extractor cannot run"
fi

# curl writes the %{http_code} itself (000 on a connection failure) *and* exits
# non-zero, so a `|| echo 000` fallback would append a second value. Take the last
# line of curl's own output instead, and default only when it printed nothing.
http_code() {
  local out
  out="$(curl -sS -o /dev/null -w '%{http_code}' -m 20 "$@" 2>/dev/null | tail -1)"
  printf '%s' "${out:-000}"
}

log "network: model API"
code="$(http_code https://api.anthropic.com/v1/models \
        -H "x-api-key: ${ANTHROPIC_API_KEY:-none}" -H 'anthropic-version: 2023-06-01')"
case "$code" in
  200) note_ok   "api.anthropic.com reachable, key accepted" ;;
  401) note_warn "api.anthropic.com reachable but the key was rejected (401)" ;;
  000) note_fail "api.anthropic.com unreachable (blocked egress or no network)" ;;
  *)   note_warn "api.anthropic.com returned HTTP $code" ;;
esac

# Every host the ingestion sources call. A blocked host does not fail the whole
# preflight — some sources are optional — but each one is named, because a source
# that silently returns nothing is indistinguishable from a company with no data.
DATA_HOSTS=(
  www.sec.gov data.sec.gov clinicaltrials.gov api.fda.gov
  eutils.ncbi.nlm.nih.gov pubmed.ncbi.nlm.nih.gov pmc.ncbi.nlm.nih.gov
  pubchem.ncbi.nlm.nih.gov api.orphadata.com data.cdc.gov data.cms.gov
  www.cms.gov data.medicaid.gov europepmc.org www.ebi.ac.uk
  labels.fda.gov www.accessdata.fda.gov doi.org
)

log "network: public data sources (${#DATA_HOSTS[@]} hosts)"
blocked=()
for host in "${DATA_HOSTS[@]}"; do
  hc="$(http_code -A 'perceptive-case-study (contact in repo README)' "https://$host/")"
  [[ "$hc" == "000" ]] && blocked+=("$host")
done
if (( ${#blocked[@]} == 0 )); then
  note_ok "all ${#DATA_HOSTS[@]} data hosts reachable"
else
  note_fail "${#blocked[@]}/${#DATA_HOSTS[@]} data hosts unreachable: ${blocked[*]}"
  printf '        ingestion will return empty for the affected sources.\n'
  printf '        On a sandboxed runner this is usually an egress policy, not a bug.\n'
fi

echo
if (( failures > 0 )); then
  die "$failures check(s) failed — see above"
fi
log "preflight passed"
