# Perceptive AI case study

A two-stage biotech research system, run over five drug developers (ABVX, KYMR, PRAX, IMVT, COGT):

- **Stage 1 — figure extraction** (`figure-extraction/`): read specified numeric values off chart/table images, each with a method and a confidence.
- **Stage 2 — investment memo** (`investment-memo/` + `backend/` + `frontend/`): from public sources only, generate a grounded investment memo (mechanism, probability of success, regulatory path, commercial opportunity, valuation) and serve it through a web app.

The ingested corpus, the ledger, one reference memo (plus its reasoning trace) per company, the figure corpus, and the market-price cache are **committed under `*/data/`**, so the API and web app run from a fresh clone with no re-ingestion and no API key. A key is only needed to *generate a new memo* or *refresh prices*.

## Prerequisites
- Python 3.9+
- Node 18+ (for the web app)
- `ANTHROPIC_API_KEY` — only to generate memos or run model-backed evals
- `ALPHAVANTAGE_API_KEY` (optional) — only to refresh the price cache (free tier: 25 calls/day)

## Setup
```bash
# Stage 2 memo engine + read API
pip install -r investment-memo/requirements.txt fastapi "uvicorn[standard]"

# Web app
cd frontend && npm install && cd ..

# Keys (only for generation / price refresh). The memo CLI reads investment-memo/.env:
echo "ANTHROPIC_API_KEY=sk-ant-..." >> investment-memo/.env
# echo "ALPHAVANTAGE_API_KEY=..."    >> investment-memo/.env   # optional

# Stage 1 is pure Python standard library — no install needed.
```

## Run
```bash
scripts/backend.sh          # read API on http://127.0.0.1:8001 (serves the committed data)
scripts/frontend.sh         # web app on http://localhost:3000  (open this)
scripts/memo.sh ABVX        # regenerate one company's memo (real model call, ~$0.40)
scripts/refresh-prices.sh   # refresh the price cache from Alpha Vantage (<=25 calls/day)
```
Start the backend first, then the frontend. The web app has three sections: **Companies** (coverage + memos), **Data** (the ingested corpus and figures), and **Activity** (generation runs + ingested datasets).

## Evaluation
Free / deterministic (no key):
```bash
cd investment-memo && PYTHONPATH=. python3 -m memo.cli eval --type retrieval --company KYMR
cd figure-extraction && PYTHONPATH=. python3 -m evaluation.run          # stub extractor
```
Model-backed (need `ANTHROPIC_API_KEY`): `memo.cli eval --type {faithfulness,epi-grounding,...}`, and `evaluation.run --extractor vlm|combined` / `evaluation.corpus_eval`. See `figure-extraction/evaluation/README.md`.

## Cost
One full memo run is ~$0.40 on `claude-sonnet-5` (the default in `scripts/memo.sh`).

## Layout
| Path | What |
|------|------|
| `investment-memo/` | Stage-2 memo engine + `memo.cli` (ingestion, analysis modules, valuation, compose) |
| `backend/` | FastAPI read API over the memo ledger |
| `frontend/` | Next.js web app |
| `figure-extraction/` | Stage-1 figure extraction + evaluation harness |
| `scripts/` | run helpers (backend / frontend / memo / refresh-prices) |
| `docs/` | design spec + the case-study briefs |

## Design & limitations
- Stage 2 architecture: `investment-memo/docs/investment-memo-architecture.md`
- Web app build spec: `docs/webapp-build-spec.md`
- Stage 1 design: `figure-extraction/docs/stage1-design.md`
- Deferred work / known limitations / where it's unreliable: `investment-memo/docs/follow-ups.md`
