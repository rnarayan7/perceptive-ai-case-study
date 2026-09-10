"""FastAPI app for the Research Workstation.

Thin read API over the memo ledger (spec §2/§3). Long jobs (ingest, compose, figure
extraction) run on the worker, not here. CORS is open to the Next dev origin; set
FRONTEND_ORIGIN in production.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.deps import DATA_ROOT, LEDGER_DB
from app.routers import activity, companies, data, feedback, figures

app = FastAPI(title="Perceptive Research OS API", version="0.1.0")

_origins = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(companies.router)
app.include_router(data.router)
app.include_router(activity.router)
app.include_router(feedback.router)
app.include_router(figures.router)


@app.get("/health")
def health():
    return {"ok": True, "data_root": str(DATA_ROOT), "ledger": LEDGER_DB}
