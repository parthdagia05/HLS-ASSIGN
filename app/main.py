"""FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload

This scaffold wires up the database lifecycle and a /health endpoint.
Feature endpoints (/suggest, /search, /cache/debug, /trending, /metrics) are
added in later phases.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from app import db
from app.models import (
    SearchRequest,
    SearchResponse,
    SuggestResponse,
    TrendingResponse,
)
from app.services import suggestions
from app.textutil import normalize_query

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


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


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    """Submit a search. Returns the dummy {"message": "Searched"} response and
    records the query so it influences future suggestions and trending.

    An empty query is rejected; everything else is normalised and recorded.
    (Phase 2 records synchronously; Phase 5 routes this through a batch writer.)
    """
    query = normalize_query(req.query)
    if not query:
        raise HTTPException(status_code=400, detail="query must not be empty")
    db.record_search(query)
    return SearchResponse(message="Searched", query=query)


@app.get("/trending", response_model=TrendingResponse)
def trending(limit: int = Query(default=10, ge=1, le=50)):
    """Currently trending searches, shown on the home page."""
    return TrendingResponse(trending=db.fetch_trending(limit))


# Serve the frontend (index.html + assets) at the site root. This mount is added
# last so it only handles paths that no API route above already claimed.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
