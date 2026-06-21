"""FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload

This scaffold wires up the database lifecycle and a /health endpoint.
Feature endpoints (/suggest, /search, /cache/debug, /trending, /metrics) are
added in later phases.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from app import db
from app.cache.distributed_cache import cache
from app.metrics import metrics
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
def suggest(
    q: str = Query(default="", description="The prefix the user typed"),
    mode: str | None = Query(
        default=None,
        description='Ranking: "basic" (all-time count) or "trending" '
        "(recency-aware). Defaults to DEFAULT_RANKING_MODE.",
    ),
):
    """Typeahead suggestions for a prefix.

    Returns up to `SUGGESTION_LIMIT` (default 10) queries that start with `q`.
    The SAME endpoint serves both rankings via `mode`, so you can compare them
    directly. Empty/whitespace prefixes and prefixes with no matches both return
    an empty list (never an error) so the UI can call this on every keystroke.
    """
    start = time.perf_counter()
    results = suggestions.get_suggestions(q, mode=mode)
    # Record end-to-end service latency (cache + DB) so /metrics can report p95.
    metrics.record_suggest_latency((time.perf_counter() - start) * 1000.0)
    return SuggestResponse(
        prefix=normalize_query(q),
        mode=suggestions.resolve_mode(mode),
        suggestions=results,
    )


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
    metrics.record_search_submission()
    db.record_search(query)
    # Rankings for this query's prefixes just changed -> drop stale cache entries.
    # (Phase 5 moves both the write and this invalidation into the batch flush.)
    cache.invalidate_query(query)
    return SearchResponse(message="Searched", query=query)


@app.get("/trending", response_model=TrendingResponse)
def trending(limit: int = Query(default=10, ge=1, le=50)):
    """Currently trending searches, shown on the home page."""
    return TrendingResponse(trending=db.fetch_trending(limit))


@app.get("/cache/debug")
def cache_debug(
    prefix: str = Query(..., description="Prefix to inspect"),
    mode: str | None = Query(default=None, description='"basic" or "trending"'),
):
    """Show which cache node owns a prefix and whether it is a hit or a miss.

    This makes the consistent-hashing routing observable: try several prefixes
    and watch them map to different nodes.
    """
    normalized = normalize_query(prefix)
    return cache.debug(mode=suggestions.resolve_mode(mode), prefix=normalized)


@app.get("/metrics")
def get_metrics():
    """Cache hit rate, DB read/write counts, suggestion latency percentiles,
    and the health of each cache node."""
    snapshot = metrics.snapshot()
    snapshot["cache"]["nodes"] = cache.node_health()
    return snapshot


# Serve the frontend (index.html + assets) at the site root. This mount is added
# last so it only handles paths that no API route above already claimed.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
