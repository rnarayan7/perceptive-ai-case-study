#!/usr/bin/env bash
# Refresh the market-data cache (price / market cap / shares) for the five covered companies
# from Alpha Vantage, writing investment-memo/data/price_snapshots.json.
# The free tier allows 25 calls/day, so run this at most once a day.
# Needs ALPHAVANTAGE_API_KEY (in investment-memo/.env or the environment); without it the
# provider degrades to a stub and the snapshot is left unchanged.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="backend:investment-memo"
exec python3 -m app.marketdata
