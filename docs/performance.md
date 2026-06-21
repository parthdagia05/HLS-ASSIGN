# Performance Report

All numbers below were measured on a local run (12-core Linux, Docker Postgres +
3 Docker Redis nodes, 120,000-query dataset). Everything here is reproducible
with the scripts in `scripts/` — re-run them and you will get comparable
figures.

> Measurement method: start a fresh server (so `/metrics` begins at zero), load
> the dataset, then run the relevant script. `/metrics` reports server-side
> truth; the benchmark also reports client-side round-trip latency.

## 1. Suggestion latency + cache hit rate

Command:
```bash
uvicorn app.main:app --port 8000      # fresh server
python scripts/benchmark.py
```

Workload: 247 distinct prefixes (length 2–4 from the dataset's head terms);
6,000 warm requests sampled from them at concurrency 12.

| Metric | Cold (cache empty → DB) | Warm (cache populated) |
|--------|-------------------------|------------------------|
| client p50 | 22.1 ms | 10.7 ms |
| client p95 | 34.6 ms | 15.0 ms |
| client p99 | 38.4 ms | 17.8 ms |
| throughput | — | ~1,100 req/s |

Server-side suggestion latency (the suggestion service itself: cache + DB,
excluding HTTP/Python overhead), from `/metrics`:

| | value |
|---|---|
| **p50** | **0.76 ms** |
| **p95** | **2.65 ms** |
| **p99** | **14.98 ms** |
| samples | 6,247 |

**Cache hit rate: 96.0%** (6,000 hits / 6,247 lookups).
**DB reads: 247** — exactly one per distinct prefix; every repeat was served from
cache. Without the cache all 6,247 lookups would have hit Postgres.

Takeaway: the cache cuts warm p95 client latency by ~2× and reduces database read
load by **25×** in this workload, and the suggestion service answers a warm
request in well under 3 ms at p95.

## 2. Write reduction via batching

Command:
```bash
python scripts/batch_demo.py
```

Workload: 2,000 `POST /search` calls spread over 20 distinct queries.

| Metric | Value |
|--------|-------|
| search submissions | 2,000 |
| **DB row writes** | **40** |
| batch flushes | 2 |
| reduction | **98.0% fewer writes (≈50× reduction)** |

A naive one-write-per-search design would issue ~2,000 UPDATEs; batching +
aggregation collapsed that to 40 (≈ distinct queries × number of flushes). The
ratio grows with traffic: the more repeated searches per flush window, the
larger the reduction.

## 3. Consistent-hashing behaviour

Command:
```bash
python scripts/cache_demo.py
```

Workload: 20,000 distinct cache keys across 3 nodes, 150 virtual nodes each.

**Distribution (should be roughly even):**

| node | keys | skew vs even split |
|------|------|--------------------|
| localhost:6391 | 6,396 | −4.1% |
| localhost:6392 | 6,283 | −5.8% |
| localhost:6393 | 7,321 | +9.8% |

**Remapping when a node is removed:** 7,321 / 20,000 = **36.6%** of keys moved —
close to the consistent-hashing target of 1/3 (33.3%). A plain `hash % N` scheme
would have remapped ~**100%** of keys, dumping the entire load onto the database
at once.

## 4. Crash recovery (durability of buffered writes)

Verified manually (see `docs/architecture.md` §6):
1. Submitted 30 searches with a long flush interval so they stayed buffered.
2. Confirmed the query was **not** yet in Postgres.
3. `kill -9` the server (no clean shutdown, no flush).
4. Restarted — the batch writer logged
   `recovered 30 searches (1 distinct) from WAL` and the count appeared in
   Postgres.

So an un-flushed buffer survives a process crash via the write-ahead log.

## 5. Summary against the non-functional requirements

| Requirement | Result |
|-------------|--------|
| Suggestions API optimised for low latency | server p95 **2.65 ms**, warm |
| Report p95 latency | ✅ above + live at `/metrics` |
| Report cache hit rate | **96.0%** |
| Report DB read/write counts | reads/writes live at `/metrics` |
| Consistent-hashing evidence | even split + ~1/N remap (`cache_demo.py`) |
| Write-reduction evidence | **≈50×** (`batch_demo.py`) |
| Runs locally, no cloud | `docker compose up` + `uvicorn` |
