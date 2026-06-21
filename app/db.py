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
