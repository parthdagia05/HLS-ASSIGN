"""FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload

This scaffold wires up the database lifecycle and a /health endpoint.
Feature endpoints (/suggest, /search, /cache/debug, /trending, /metrics) are
added in later phases.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query

from app import db
from app.models import SuggestResponse
from app.services import suggestions
from app.textutil import normalize_query


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook. We open the DB pool (and ensure the schema)
    before serving traffic, and close it cleanly on shutdown."""
    db.init()
    yield
    db.close()


app = FastAPI(
    title="Search Typeahead",
    description="Typeahead suggestions with a distributed cache, recency-aware "
    "trending, and batched writes.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    """Liveness probe: confirms the API is up and the database is reachable."""
    return {"status": "ok", "queries_loaded": db.row_count()}


@app.get("/suggest", response_model=SuggestResponse)
def suggest(q: str = Query(default="", description="The prefix the user typed")):
    """Typeahead suggestions for a prefix.

    Returns up to `SUGGESTION_LIMIT` (default 10) queries that start with `q`,
    sorted by popularity. Empty/whitespace prefixes and prefixes with no matches
    both return an empty list (never an error) so the UI can call this on every
    keystroke safely.
    """
    results = suggestions.get_suggestions(q)
    return SuggestResponse(prefix=normalize_query(q), suggestions=results)
