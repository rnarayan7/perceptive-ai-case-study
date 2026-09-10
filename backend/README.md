# Perceptive Research OS — Backend (FastAPI)

Thin read API over the memo ledger. v1 scope per `docs/webapp-build-spec.md` ("Lean v1"):
SQLite `ledger.db` + rendered-memo JSON, no Postgres, no object storage. Long jobs
(ingest, compose, figure extraction) run on the worker, not here.

## Run (dev)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../investment-memo -e .      # installs the `memo` engine + this app
export MEMO_DATA_ROOT=../investment-memo/data   # default; holds ledger.db + memos/
uvicorn app.main:app --reload --port 8001   # 8000 is used by the Stage-1 label server
# docs at http://127.0.0.1:8001/docs   (use 127.0.0.1, not localhost — Node fetch/IPv6)
```

The frontend defaults to `http://127.0.0.1:8001`; override with `NEXT_PUBLIC_API_BASE`.
```

## Endpoints (v1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + resolved data root |
| GET | `/api/companies` | coverage dashboard rows (thesis, conviction dots, fair value) |
| GET | `/api/companies/{ticker}` | company detail: four-question metrics + KPIs |
| GET | `/api/companies/{ticker}/memo` | latest rendered memo |
| GET | `/api/memos/{memo_id}` | rendered memo by id |
| GET | `/api/evidence/{evidence_id}` | audit-drawer target (evidence → claim reverse lookup) |
| GET | `/api/activity/generations` | generation runs with cost/summary |

## Known v1 gaps (see spec)

- `market_price` / `market_cap` / `upside_pct` are `null` until the Alpha Vantage adapter
  (`MarketDataProvider`, §7.4) is wired.
- `cash` / `runway` KPIs are `null` until the price module emits structured claim values.
- Documents, figures, and search endpoints are not built yet (later phases).
