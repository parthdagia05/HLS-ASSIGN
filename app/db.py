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

from psycopg_pool import ConnectionPool

from app.config import settings
from app.metrics import metrics
from app.textutil import like_prefix_pattern

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


def fetch_suggestions(prefix: str, limit: int) -> list[dict]:
    """Top `limit` queries that start with `prefix`, most popular first.

    This is the "basic" ranking required for the core marks: pure all-time
    popularity (ORDER BY count DESC). The recency-aware "trending" ranking is
    layered on top of this in Phase 4.

    `prefix` must already be normalised (lowercased/trimmed). The caller is
    responsible for that; see app.services.suggestions.
    """
    pattern = like_prefix_pattern(prefix)
    with pool.connection() as conn:
        rows = conn.execute(
            r"""
            SELECT query, count
            FROM queries
            WHERE query LIKE %s ESCAPE '\'
            ORDER BY count DESC, query ASC
            LIMIT %s
            """,
            (pattern, limit),
        ).fetchall()
    # rows are tuples (query, count); shape them into plain dicts for the API.
    return [{"query": q, "count": c} for q, c in rows]


def record_search(query: str) -> None:
    """Record one submitted search by incrementing its count.

    NOTE: this is the *naive synchronous* write -- one DB round trip per search.
    It is correct and simple, and it is exactly the baseline that Phase 5
    (batch writes) improves upon. A brand-new query is inserted with count = 1.
    """
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO queries (query, count, last_searched_at)
            VALUES (%s, 1, now())
            ON CONFLICT (query) DO UPDATE
                SET count = queries.count + 1,
                    last_searched_at = now()
            """,
            (query,),
        )
    metrics.record_db_write(1)


def fetch_trending(limit: int) -> list[dict]:
    """Top `limit` queries overall, most popular first.

    Phase 2 version: pure all-time popularity. Phase 4 replaces this with a
    recency-aware ranking so genuinely *trending* (recently hot) queries surface.
    """
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT query, count FROM queries ORDER BY count DESC, query ASC LIMIT %s",
            (limit,),
        ).fetchall()
    return [{"query": q, "count": c} for q, c in rows]
