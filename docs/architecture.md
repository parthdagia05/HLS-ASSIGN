# Architecture

This document explains how the Search Typeahead system is put together, the
design choices behind each part, and the trade-offs they involve. It is the
companion to the code — every component named here maps to a file under `app/`.

## 1. High-level diagram

```
                ┌───────────────────────────────────────────────┐
                │                Browser (frontend/)            │
                │  search box · debounced /suggest · keyboard   │
                │  nav · trending chips · basic/trending toggle │
                └───────────────┬───────────────────────────────┘
                                │  HTTP (JSON)
                                ▼
        ┌───────────────────────────────────────────────────────────┐
        │                    FastAPI app (app/main.py)                │
        │                                                             │
        │  GET  /suggest   ──►  suggestions service                   │
        │  POST /search    ──►  batch writer (buffer)                 │
        │  GET  /trending  ──►  db.fetch_trending                     │
        │  GET  /cache/debug, /metrics, POST /batch/flush             │
        └───────┬───────────────────────────┬───────────────┬────────┘
                │ read path                  │ write path    │ metrics
                ▼                            ▼               ▼
   ┌────────────────────────┐   ┌────────────────────┐  ┌──────────────┐
   │  Distributed cache      │   │   Batch writer      │  │  Metrics     │
   │  (app/cache/)           │   │  (services/         │  │ (app/        │
   │                         │   │   batch_writer.py)  │  │  metrics.py) │
   │  consistent-hash ring   │   │  buffer + aggregate │  │ hit rate,    │
   │  ┌──────┐┌──────┐┌────┐ │   │  + WAL + flush      │  │ DB r/w,      │
   │  │redis1││redis2││r3 │ │   └─────────┬──────────┘  │ p50/95/99    │
   │  └──────┘└──────┘└────┘ │             │ one batched │              │
   └───────────┬─────────────┘             │ write/flush └──────────────┘
               │ miss ↓ populate           ▼
               └──────────────►  ┌────────────────────────────┐
                                 │   PostgreSQL (app/db.py)     │
                                 │   table `queries`            │
                                 │   query, count, recent_score │
                                 │   + prefix index             │
                                 └────────────────────────────┘
```

Two independent paths:

* **Read path (suggestions)** — optimised for latency. Cache first, database
  only on a miss.
* **Write path (search submissions)** — optimised for throughput. Buffer in
  memory, aggregate, and write to the database in batches.

## 2. Data model (`app/db.py`)

A single table keeps the system easy to reason about:

| column                    | meaning |
|---------------------------|---------|
| `query` (PK)              | the search string, stored normalised (lowercased, trimmed) |
| `count`                   | all-time number of searches — "historical popularity" |
| `recent_score`            | time-decayed recent activity — drives trending |
| `recent_score_updated_at` | when `recent_score` was last refreshed (decay anchor) |
| `last_searched_at`        | last submission time (diagnostics) |

The index `idx_queries_prefix ON queries (query text_pattern_ops)` makes
`WHERE query LIKE 'prefix%'` use an index even on a UTF-8 database, which is what
keeps prefix lookups fast at 120k rows.

**Why one Postgres table instead of a trie / specialised engine?** The dataset
is small enough (100k–1M rows) that an indexed prefix scan returns the top 10 in
single-digit milliseconds, and a relational table makes counts, recency, and
batched upserts trivial and durable. The cache (below) absorbs the read volume,
so the database is rarely the bottleneck. A trie or a search engine
(Elasticsearch) would be the next step at much larger scale.

## 3. Suggestion read path (`app/services/suggestions.py`)

```
normalise(prefix)
  └─ empty? → []
  └─ cacheable (len ≤ MAX_PREFIX_LEN)?
        └─ cache.get(mode, prefix)  → HIT  → return
                                     → MISS → db.fetch_suggestions → cache.set → return
```

* **Normalisation** is shared with the loader (`app/textutil.py`) so stored
  queries and lookups can never disagree on case/whitespace.
* **Only short prefixes are cached** (≤ `MAX_PREFIX_LEN`, default 12). Short
  prefixes are typed by everyone and repeat constantly (high cache value); long
  prefixes are nearly unique, so caching them would waste memory for almost no
  hit rate. Long prefixes go straight to the database.

## 4. Distributed cache + consistent hashing (`app/cache/`)

The cache is several Redis nodes. We do **not** use Redis Cluster — instead the
app routes each key to a node with a **consistent-hash ring**
(`consistent_hash.py`), and `distributed_cache.py` holds one client per node.

**Why consistent hashing and not `hash(key) % N`?** With modulo, adding or
removing a node changes the bucket for almost every key, so virtually the entire
cache is invalidated at once (a "cache stampede" onto the database). Consistent
hashing places nodes and keys on a circle; a key belongs to the next node
clockwise. Adding/removing a node only moves the keys in that node's arc — on
average `1/N` of them.

**Virtual nodes.** Each physical node is placed at many points on the ring
(`VIRTUAL_NODES`, default 150) so the arcs — and therefore the load — are
spread evenly. `scripts/cache_demo.py` demonstrates both properties with real
numbers (even distribution; only ~1/N keys move when a node is removed).

**Resilience.** The cache is a best-effort accelerator. If a node is
unreachable, the error is swallowed, counted as a miss, and the request falls
back to the database — a cache outage never takes the API down.

**Freshness.** Two mechanisms keep cached lists from going stale:
1. a **TTL** on every entry (`CACHE_TTL_SECONDS`, default 30s) — a backstop;
2. **explicit invalidation** — when a query's counts change, we delete the
   cached entries for all of its prefixes in both ranking modes
   (`cache.invalidate_query`). In the batched design this happens once per flush.

`GET /cache/debug?prefix=` exposes the routing: it shows which node owns a
prefix and whether it is currently a hit or a miss.

## 5. Trending searches — recency-aware ranking (`app/db.py`)

The same `GET /suggest` endpoint serves two rankings, selected by `?mode=`:

* **basic** — `ORDER BY count DESC`. Pure all-time popularity (the core-marks
  behaviour).
* **trending** — `ORDER BY (count + α · decayed_recent_score) DESC`.

The assignment asks five specific questions; here are the answers:

1. **How are recent searches tracked?** Each query row has `recent_score`. On
   every search we *decay* the stored score by the elapsed time and then add the
   new activity:
   `recent_score = recent_score · e^(−λ·Δt) + delta`, and reset the timestamp.
   So the score reflects *recent* volume, not lifetime volume. `λ = ln 2 /
   half_life`, with `RECENCY_HALFLIFE_SECONDS` default 600s (10 min).

2. **How does recency affect ranking?** The trending score adds
   `α · decayed_recent_score` to the historical `count` (`RECENCY_ALPHA` scales
   recent activity to be comparable to all-time counts). A query searched a lot
   in the last few minutes gets a large temporary boost and rises in the list.

3. **How do we avoid permanently over-ranking a brief spike?** Because the score
   *decays exponentially*, once the burst stops the boost halves every
   half-life and the query slides back toward its count-based position. Nothing
   stays elevated on the strength of a short-lived spike. `scripts/trending_demo.py`
   shows a tail query surging to #1 under trending right after a burst, while the
   basic ranking barely moves.

4. **How is the cache updated/invalidated when rankings change?** A change to a
   query invalidates its prefixes' cached entries (`cache.invalidate_query`),
   and the short TTL is a backstop. Trending and basic use separate cache keys
   so they never contaminate each other.

5. **Trade-offs (freshness vs latency vs complexity).** A short TTL / aggressive
   invalidation = fresher trending but more cache misses (higher latency, more
   DB load). A longer TTL = faster and cheaper but more stale. The decay is
   computed in SQL at query time, which keeps writes simple (just a number per
   row) at the cost of a tiny bit of math per read. We chose a 30s TTL +
   per-flush invalidation + 10-min half-life as a balanced default; all are
   single config values in `app/config.py`.

## 6. Batch writes (`app/services/batch_writer.py`)

Writing to Postgres on every `POST /search` is wasteful: a query searched 1,000
times would cause 1,000 UPDATEs of one row. Instead:

```
POST /search → batch_writer.submit(query)      # in-memory Counter bump (+ WAL append)
                          │
   flush trigger: every FLUSH_INTERVAL_SECONDS  OR  BATCH_SIZE distinct queries
                          ▼
          db.apply_batch(aggregated)            # ONE multi-row upsert for the whole batch
          cache.invalidate_query(...) per query
```

Repeated queries are **aggregated** in the buffer, so 1,000 searches of one
query become a single `+1000`. `scripts/batch_demo.py` measures ~**50× fewer
database writes** (2,000 searches → 40 writes).

### Failure trade-offs (what if it crashes before a flush?)

The buffer is in memory, so a crash would lose un-flushed counts. We bound that
with an optional **write-ahead log (WAL)**:

* every submission is appended to `data/batch.wal` before it is acknowledged;
* on flush we **atomically rename** the WAL aside, write the batch to Postgres,
  and only then delete the renamed file;
* on startup we **replay** any leftover WAL into the database (verified: 30
  buffered searches survived a `kill -9` and were recovered on restart).

Honest limitations, by design:
* We `flush()` to the OS but do **not** `fsync` on every write — a process crash
  is covered, but a power/OS crash can still lose the last few entries. This
  trades a little durability for a lot of throughput (per-write fsync is slow).
* Recovery is **at-least-once**: a crash in the small window after the DB commit
  but before deleting the rotated WAL replays that batch again (slight
  over-count). Exactly-once would require idempotent writes (e.g. dedup keys).
* With `BATCH_WAL_ENABLED=false` the writer is pure in-memory: faster, but a
  crash loses the whole un-flushed buffer. This is the freshness/throughput vs
  durability knob.

## 7. Concurrency model

The app is intentionally synchronous and simple to reason about:
* FastAPI runs the sync endpoints in a threadpool, so requests are served
  concurrently.
* PostgreSQL access goes through a connection pool (`psycopg_pool`).
* The batch writer runs on its own daemon thread; `metrics` and the buffer are
  guarded by locks. There is no asyncio, so there are no `await` subtleties to
  explain.

## 8. Metrics (`app/metrics.py`)

A lock-guarded singleton counts cache hits/misses, DB reads/writes, search
submissions, and batch flushes, and keeps a bounded window of `/suggest`
latencies for p50/p95/p99. `GET /metrics` returns a snapshot plus per-node cache
health. See [performance.md](performance.md) for measured numbers.
