#!/usr/bin/env bash
# Run the Stage 1 figure-extraction evaluation over the six gold figures.
# Default is the model-free stub: no API key, no install.
# Pass an extractor to use a model (needs ANTHROPIC_API_KEY in figure-extraction/.env):
#   scripts/figures.sh            # stub (default)
#   scripts/figures.sh vlm        # vision-model read
#   scripts/figures.sh combined   # vision read cross-checked against the CV readers
set -euo pipefail
cd "$(dirname "$0")/../figure-extraction"
exec python3 -m evaluation.run --extractor "${1:-stub}"
