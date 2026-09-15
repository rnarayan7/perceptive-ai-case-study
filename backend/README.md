# Backend — read API (FastAPI)

A thin read API over the memo ledger (`investment-memo/data/ledger.db`) and the rendered memos, plus market data and the figure corpus. It serves the committed data snapshot, so it runs from a fresh clone with no API key. Generation (ingest, compose) happens in the memo CLI, not here.

## Run
From the repo root:
```bash
scripts/backend.sh        # http://127.0.0.1:8001  (interactive docs at /docs)
```
That runs `PYTHONPATH="backend:investment-memo" uvicorn app.main:app --port 8001`. Use `127.0.0.1`, not `localhost` (the frontend's Node fetch prefers IPv4). Override the port with `PORT`.

To run it in a venv instead:
```bash
pip install -e investment-memo fastapi "uvicorn[standard]"
PYTHONPATH="backend:investment-memo" uvicorn app.main:app --reload --port 8001
```

## Endpoints
Full list and schemas at `/docs`. The groups:

| Path | Purpose |
|---|---|
| `GET /health` | liveness + resolved data root |
| `GET /api/companies`, `/api/companies/{ticker}` | coverage rows, and company detail (four-question metrics + KPIs) |
| `GET /api/companies/{ticker}/memo`, `/api/memos/{memo_id}` | rendered memo, latest or by id |
| `GET /api/evidence/{evidence_id}` | evidence-to-claim reverse lookup (the audit drawer) |
| `GET /api/documents`, `/api/documents/{doc_id}` | ingested source documents |
| `GET /api/figures`, `/api/figures/{company}/{ref}` | the ingested figure corpus and images |
| `GET /api/activity/generations`, `/api/activity/ingestion` | generation runs and ingested datasets |
| `POST /api/feedback/chat` | section feedback chat, written to the ledger |

## Market data
`market_price` / `market_cap` come from `app/marketdata.py`. With `ALPHAVANTAGE_API_KEY` set, the committed `price_snapshots.json` is served (and `scripts/refresh-prices.sh` updates it from Alpha Vantage); without a key they are `null`. The request path never calls out to the network; only the refresh job does.
