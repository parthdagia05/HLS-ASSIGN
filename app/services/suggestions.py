"""Suggestion service: the logic behind GET /suggest.

Read path (Phase 3 adds the cache):

    normalise prefix
      -> cacheable? look in the distributed cache
           HIT  -> return cached list          (no database touched)
           MISS -> read PostgreSQL, store in cache, return

Recency-aware ranking ("trending" mode) is layered on in Phase 4; for now the
ranking is always "basic" (all-time count).
"""

from __future__ import annotations

from app import db
from app.cache.distributed_cache import cache
from app.config import settings
from app.metrics import metrics
from app.textutil import normalize_query

# Phase 3 always serves the basic ranking. The mode is still threaded through
# the cache key so Phase 4 can add a separate "trending" cache namespace without
# colliding with these entries.
_MODE = "basic"


def get_suggestions(prefix: str, limit: int | None = None) -> list[dict]:
    """Return up to `limit` suggestions for `prefix`, serving from cache when
    possible.

    Edge cases (empty/whitespace prefix, no matches) still return [].

    Only prefixes up to MAX_PREFIX_LEN are cached: short prefixes are typed by
    many users and repeat constantly (high cache value), whereas long prefixes
    are nearly unique (caching them would just waste memory). Long prefixes go
    straight to the database.
    """
    limit = limit or settings.suggestion_limit
    normalized = normalize_query(prefix)
    if not normalized:
        return []

    cacheable = len(normalized) <= settings.max_prefix_len

    if cacheable:
        cached = cache.get(_MODE, normalized)
        if cached is not None:
            return cached[:limit]

    # Cache miss (or non-cacheable prefix): read the primary store.
    rows = db.fetch_suggestions(normalized, limit)
    metrics.record_db_read()

    if cacheable:
        cache.set(_MODE, normalized, rows)
    return rows
