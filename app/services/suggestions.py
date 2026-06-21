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

VALID_MODES = {"basic", "trending"}


def resolve_mode(mode: str | None) -> str:
    """Pick the ranking mode. Falls back to the configured default for missing
    or unrecognised values, so a bad ?mode= never errors."""
    return mode if mode in VALID_MODES else settings.default_ranking_mode


def get_suggestions(
    prefix: str, mode: str | None = None, limit: int | None = None
) -> list[dict]:
    """Return up to `limit` suggestions for `prefix`, serving from cache when
    possible.

    `mode` selects ranking: "basic" (all-time count) or "trending"
    (recency-aware). The two modes are cached under separate keys so they never
    contaminate each other.

    Edge cases (empty/whitespace prefix, no matches) still return [].

    Only prefixes up to MAX_PREFIX_LEN are cached: short prefixes are typed by
    many users and repeat constantly (high cache value), whereas long prefixes
    are nearly unique (caching them would just waste memory). Long prefixes go
    straight to the database.
    """
    mode = resolve_mode(mode)
    limit = limit or settings.suggestion_limit
    normalized = normalize_query(prefix)
    if not normalized:
        return []

    cacheable = len(normalized) <= settings.max_prefix_len

    if cacheable:
        cached = cache.get(mode, normalized)
        if cached is not None:
            return cached[:limit]

    # Cache miss (or non-cacheable prefix): read the primary store.
    rows = db.fetch_suggestions(normalized, limit, mode=mode)
    metrics.record_db_read()

    if cacheable:
        cache.set(mode, normalized, rows)
    return rows
