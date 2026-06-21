"""PostgreSQL access layer.

This module owns:
  * the connection pool (so we reuse connections instead of paying the
    connect cost on every request -- critical for low-latency suggestions),
  * the schema definition (one `queries` table), and
  * the raw SQL helpers used by the rest of the app.

We use psycopg 3 with plain SQL rather than an ORM so that every database
operation is visible and explainable.

Schema (single table `queries`):
    query                    TEXT  -- the search string, stored lowercased
    count                    BIGINT -- all-time popularity (historical)
    recent_score             FLOAT  -- time-decayed recent activity (Phase 4)
    recent_score_updated_at  TIMESTAMPTZ -- when recent_score was last touched
    last_searched_at         TIMESTAMPTZ -- last time this query was submitted
"""

from __future__ import annotations

import math

from psycopg_pool import ConnectionPool

from app.config import settings
from app.metrics import metrics
from app.textutil import like_prefix_pattern

# Exponential-decay constant derived from the configured half-life.
# A query's recent_score decays by half every `recency_halflife_seconds`.
#   decay(age) = exp(-DECAY_LAMBDA * age)   and   exp(-LAMBDA * halflife) = 0.5
DECAY_LAMBDA = math.log(2) / settings.recency_halflife_seconds

# SQL fragment: the recent_score decayed to "now". Reused by trending ranking
# and by the trending list. recent_score_updated_at records when the stored
# recent_score was last refreshed, so we decay from that moment to now().
# Columns are qualified with `queries.` so the same fragment is unambiguous both
# in a SELECT and inside an ON CONFLICT ... DO UPDATE (where bare `recent_score`
# could mean either the existing row or the proposed EXCLUDED row).
_DECAYED_RECENT = (
    "queries.recent_score * "
    "exp(-%(lam)s * EXTRACT(EPOCH FROM (now() - queries.recent_score_updated_at)))"
)

# A process-wide connection pool. `open=True` connects lazily on first use.
# min/max sizing is modest -- enough for a local demo with concurrent requests.
pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=2,
    max_size=10,
    open=False,  # opened explicitly in init() so startup failures are obvious
    name="typeahead-pool",
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS queries (
    query                    TEXT PRIMARY KEY,
    count                    BIGINT NOT NULL DEFAULT 0,
    recent_score             DOUBLE PRECISION NOT NULL DEFAULT 0,
    recent_score_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_searched_at         TIMESTAMPTZ
);

-- text_pattern_ops makes  WHERE query LIKE 'prefix%'  use the index even on a
-- C/UTF-8 locale database. This is what keeps prefix lookups fast at 100k+ rows.
CREATE INDEX IF NOT EXISTS idx_queries_prefix
    ON queries (query text_pattern_ops);
"""


def init() -> None:
    """Open the pool and ensure the schema exists. Call once at startup."""
    pool.open()
    pool.wait()  # block until the minimum number of connections is ready
    with pool.connection() as conn:
        conn.execute(SCHEMA_SQL)


def close() -> None:
    """Close the pool. Call once at shutdown."""
    pool.close()


def row_count() -> int:
    """Number of rows in `queries`. Used by the dataset loader and /metrics."""
    with pool.connection() as conn:
        result = conn.execute("SELECT count(*) FROM queries").fetchone()
        return int(result[0]) if result else 0


def fetch_suggestions(prefix: str, limit: int, mode: str = "basic") -> list[dict]:
    r"""Top `limit` queries that start with `prefix`.

    Two ranking modes share this one query (and the same /suggest API):

      * "basic"    -> ORDER BY count DESC. Pure all-time popularity. This is the
                      core-marks behaviour: historically popular queries first.

      * "trending" -> ORDER BY (count + alpha * decayed_recent_score) DESC.
                      Recently searched queries get a temporary boost on top of
                      their historical count. Because the recent score decays,
                      a short-lived spike fades back to the count-based order
                      over time (see DECAY_LAMBDA) -- so nothing stays
                      over-ranked permanently.

    `prefix` must already be normalised. The score expression is computed only
    over the rows matching the prefix (a small set), so this stays fast.
    """
    pattern = like_prefix_pattern(prefix)
    if mode == "trending":
        order_by = f"(count + %(alpha)s * {_DECAYED_RECENT}) DESC, count DESC, query ASC"
    else:
        order_by = "count DESC, query ASC"

    sql = (
        r"SELECT query, count FROM queries "
        r"WHERE query LIKE %(pattern)s ESCAPE '\' "
        f"ORDER BY {order_by} "
        r"LIMIT %(limit)s"
    )
    params = {
        "pattern": pattern,
        "limit": limit,
        "alpha": settings.recency_alpha,
        "lam": DECAY_LAMBDA,
    }
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"query": q, "count": c} for q, c in rows]


def record_search(query: str, delta: int = 1) -> None:
    """Record `delta` searches for one query (count + recency).

    Two things happen to the row:
      * count           += delta              (all-time popularity)
      * recent_score     = decay(old) + delta (recency, for trending)
        and recent_score_updated_at is reset to now().

    The "decay(old)" step is the key idea: before adding new activity we shrink
    the previously stored recent_score by how much time has passed, so the score
    always reflects *recent* volume rather than total volume. A new query starts
    with recent_score = delta.

    NOTE: this is still the *naive synchronous* write (one DB round trip).
    Phase 5 keeps this exact SQL but feeds it pre-aggregated batches instead of
    one call per search. `delta` already supports that.
    """
    with pool.connection() as conn:
        conn.execute(
            f"""
            INSERT INTO queries (query, count, recent_score,
                                 recent_score_updated_at, last_searched_at)
            VALUES (%(query)s, %(delta)s, %(delta)s, now(), now())
            ON CONFLICT (query) DO UPDATE
                SET count = queries.count + %(delta)s,
                    recent_score = {_DECAYED_RECENT} + %(delta)s,
                    recent_score_updated_at = now(),
                    last_searched_at = now()
            """,
            {"query": query, "delta": delta, "lam": DECAY_LAMBDA},
        )
    metrics.record_db_write(1)


def apply_batch(aggregated: dict[str, int]) -> int:
    """Apply a whole batch of search counts in ONE database round trip.

    `aggregated` maps query -> number of times it was searched in this batch
    (already summed by the batch writer). We build a single multi-row
    INSERT ... ON CONFLICT DO UPDATE so that, no matter how many searches were
    submitted, the database sees exactly one statement per flush.

    This is the same per-query maths as record_search (count += delta, recent
    score decayed-then-bumped), just applied to many queries at once.

    Returns the number of distinct queries written (== rows in the statement).
    """
    if not aggregated:
        return 0

    # All parameters are passed by NAME (q0, c0, q1, c1, ...) so they can coexist
    # with the named %(lam)s used inside the _DECAYED_RECENT fragment. (psycopg
    # does not allow mixing positional %s and named %(...)s in one query.)
    params: dict = {"lam": DECAY_LAMBDA}
    row_placeholders = []
    for i, (query, delta) in enumerate(aggregated.items()):
        params[f"q{i}"] = query
        params[f"c{i}"] = delta  # count delta == recent_score delta for this query
        row_placeholders.append(f"(%(q{i})s, %(c{i})s, %(c{i})s, now(), now())")
    values_sql = ", ".join(row_placeholders)

    sql = f"""
        INSERT INTO queries (query, count, recent_score,
                             recent_score_updated_at, last_searched_at)
        VALUES {values_sql}
        ON CONFLICT (query) DO UPDATE
            SET count = queries.count + EXCLUDED.count,
                recent_score = {_DECAYED_RECENT} + EXCLUDED.recent_score,
                recent_score_updated_at = now(),
                last_searched_at = now()
    """
    with pool.connection() as conn:
        conn.execute(sql, params)

    written = len(aggregated)
    metrics.record_db_write(written)
    return written


def fetch_trending(limit: int) -> list[dict]:
    """Top `limit` *trending* queries: ranked by decayed recent activity, with
    all-time count as a tie-breaker.

    On a freshly loaded dataset nothing has been searched yet, so every
    recent_score is 0 and the tie-breaker makes this fall back to "most popular
    overall" -- a sensible default. As users search, recently hot queries rise
    to the top and then fade again as their recent_score decays.
    """
    sql = (
        f"SELECT query, count FROM queries "
        f"ORDER BY ({_DECAYED_RECENT}) DESC, count DESC, query ASC "
        f"LIMIT %(limit)s"
    )
    with pool.connection() as conn:
        rows = conn.execute(sql, {"limit": limit, "lam": DECAY_LAMBDA}).fetchall()
    return [{"query": q, "count": c} for q, c in rows]
