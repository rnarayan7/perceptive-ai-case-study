# Running this from scratch

Exact steps to run the submitted system from a fresh clone. Every command below was run from a clean clone before submitting.

## Prerequisites
- Python 3.9+
- Node 18+ (only for the web app)
- Optional keys:
  - `ANTHROPIC_API_KEY` to generate a new memo or run model-backed evals.
  - `ALPHAVANTAGE_API_KEY` to refresh the price cache.

The web app and both test suites run with **no key**: the ingested corpus, the ledger, one memo per company, and the price cache are committed under `*/data/`.

## 0. Clone
```bash
git clone https://github.com/rnarayan7/perceptive-ai-case-study
cd perceptive-ai-case-study
```

## 1. Stage 1 — figure extraction (no install, no key)
```bash
scripts/figures.sh                 # model-free run over the six gold figures
scripts/figures.sh vlm             # vision-model read (needs ANTHROPIC_API_KEY)
cd figure-extraction && python3 -m pytest evaluation/tests    # 42 tests
```

## 2. Stage 2 — the web app (runs from committed data, no key)
Install once:
```bash
pip install -r investment-memo/requirements.txt fastapi "uvicorn[standard]"
cd frontend && npm install && cd ..
```
Run two terminals:
```bash
# terminal A — read API on http://127.0.0.1:8001
scripts/backend.sh

# terminal B — web app on http://localhost:3000  (open this)
scripts/frontend.sh
```
Start the backend first. The frontend reads the API base from `NEXT_PUBLIC_API_BASE` (default `http://127.0.0.1:8001`); if you serve the backend elsewhere, set that variable before `scripts/frontend.sh` runs its build, since Next.js inlines it at build time.

## 3. Generate a fresh memo (needs a key)
```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> investment-memo/.env
scripts/memo.sh ABVX               # ~$0.40, ~4 min on claude-sonnet-5; writes to the ledger
```
`ABVX KYMR PRAX IMVT COGT` are the covered companies.

## 4. Evaluate
```bash
cd investment-memo
# free / deterministic, no key:
PYTHONPATH=. python3 -m memo.cli eval --type retrieval --company KYMR
# Stage 2 offline tests (186):
PYTHONPATH=. python3 -m pytest -m "not network"
```
Model-backed evals: `--type faithfulness | memo | epi-grounding | judge-probe | known-bad` (need `ANTHROPIC_API_KEY`).

## Ports
Backend `8001`, frontend `3000`. Override the backend with `PORT`, the frontend with `PORT` + `NEXT_PUBLIC_API_BASE`.

## Verified from a clean clone
- Stage 1 model-free run and its 42 tests.
- `pip install`, then Stage 2's 186 offline tests, the memo CLI, and a free retrieval eval.
- Backend served over HTTP (`/health`, `/api/companies`, `/api/companies/{ticker}/memo`).
- Frontend `npm install` + production build, then the homepage and a company page server-rendered live from the backend.

Not run in that check (need a key or cost money): composing a new memo, the vision-model figure extractors, and the judge-based evals.
