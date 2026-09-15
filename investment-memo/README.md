# Stage 2 — Investment memo

From public sources only, generates a grounded, cited investment memo on a drug developer (mechanism, probability of success, regulatory path, peak sales, valuation) and serves it through a web app. Covers ABVX, KYMR, PRAX, IMVT, COGT.

The corpus, ledger, one reference memo per company, and the price cache are committed under `data/`, so the read API and web app run from a fresh clone with **no API key**. A key is only needed to generate a new memo or refresh prices.

## Requirements
- Python 3.9+
- Node 18+ (only for the web app)
- `ANTHROPIC_API_KEY` to compose a memo or run model-backed evals (in `investment-memo/.env` or the environment)
- `ALPHAVANTAGE_API_KEY` optional, only to refresh the price cache

## Install
From the repo root:
```bash
pip install -r investment-memo/requirements.txt     # engine + memo CLI
pip install fastapi "uvicorn[standard]"             # read API
cd frontend && npm install && cd ..                 # web app
```

## Generate a memo
```bash
# real, paid model calls; the wrapper uses claude-sonnet-5 (~$0.40/run)
scripts/memo.sh ABVX
# equivalent: cd investment-memo && PYTHONPATH=. python3 -m memo.cli compose --company ABVX
```
The CLI (`PYTHONPATH=. python3 -m memo.cli --help`) also exposes `ingest`, `search`, `trials`, `filings`, `analyze`, `eval`, and `calibrate`.

## Run the app
```bash
scripts/backend.sh     # read API on http://127.0.0.1:8001 (serves committed data, no key)
scripts/frontend.sh    # web app on http://localhost:3000
```
Start the backend first. `scripts/refresh-prices.sh` refreshes the price cache from Alpha Vantage.

## Evaluate
```bash
cd investment-memo
# free / deterministic, no key:
PYTHONPATH=. python3 -m memo.cli eval --type retrieval --company KYMR
# model-backed: --type faithfulness | memo | epi-grounding | judge-probe | known-bad
```

## Tests
```bash
cd investment-memo
PYTHONPATH=. python3 -m pytest -m "not network"
```

## Design & limitations
- The approach and how it works: `docs/stage2-approach.html`
- Deferred work and where it is unreliable: `docs/follow-ups.md`
