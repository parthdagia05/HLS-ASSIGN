"""Suggestion service: the logic behind GET /suggest.

In Phase 1 this is intentionally thin -- normalise the prefix, handle the empty
case, and ask the database for the top matches. Later phases wrap this with a
distributed cache (Phase 3) and add recency-aware ranking (Phase 4).
"""

from __future__ import annotations

from app import db
from app.config import settings
from app.textutil import normalize_query


def get_suggestions(prefix: str, limit: int | None = None) -> list[dict]:
    """Return up to `limit` suggestions for `prefix`, most popular first.

    Edge cases handled here so callers never have to:
      * missing / empty / whitespace-only prefix -> [] (nothing to suggest)
      * mixed-case / padded input -> normalised before lookup
      * no matches -> [] (the DB query simply returns nothing)
    """
    limit = limit or settings.suggestion_limit
    normalized = normalize_query(prefix)
    if not normalized:
        return []
    return db.fetch_suggestions(normalized, limit)
