# API Documentation

Base URL (local): `http://localhost:8000`

Interactive, auto-generated docs (try requests in the browser): `/docs`.

All responses are JSON. Query strings are normalised server-side (trimmed,
lowercased) before use.

---

## GET /suggest

Typeahead suggestions for a prefix. This is the core, latency-critical endpoint.

| Query param | Required | Default | Description |
|-------------|----------|---------|-------------|
| `q`         | no       | `""`    | The prefix the user typed. |
| `mode`      | no       | `DEFAULT_RANKING_MODE` (`trending`) | `basic` (all-time count) or `trending` (recency-aware). Unknown values fall back to the default. |

Returns up to `SUGGESTION_LIMIT` (default 10) matches, sorted by the chosen
ranking. An empty/whitespace prefix or a prefix with no matches returns an empty
list (HTTP 200, never an error).

```bash
curl "http://localhost:8000/suggest?q=iph&mode=trending"
```
```json
{
  "prefix": "iph",
  "mode": "trending",
  "suggestions": [
    { "query": "iphone", "count": 5418280 },
    { "query": "iphone price", "count": 87916 }
  ]
}
```

---

## POST /search

Submit a search. Returns the dummy response and records the query (via the batch
writer — no synchronous DB write). New queries are created on first search.

Request body:
```json
{ "query": "iphone 15" }
```
Response:
```json
{ "message": "Searched", "query": "iphone 15" }
```
* Empty/whitespace `query` → HTTP 422/400.
* The recorded count/recency is reflected in suggestions and trending after the
  next batch flush (within `FLUSH_INTERVAL_SECONDS`, or immediately via
  `POST /batch/flush`).

```bash
curl -X POST http://localhost:8000/search \
     -H 'Content-Type: application/json' \
     -d '{"query":"iphone 15"}'
```

---

## GET /trending

Currently trending searches (ranked by decayed recent activity, with all-time
count as a tie-breaker, so it falls back to "most popular" on a fresh dataset).

| Query param | Required | Default | Description |
|-------------|----------|---------|-------------|
| `limit`     | no       | 10      | 1–50. |

```bash
curl "http://localhost:8000/trending?limit=10"
```
```json
{ "trending": [ { "query": "iphone", "count": 5418280 } ] }
```

---

## GET /cache/debug

Show which cache node owns a prefix and whether it is currently cached. Makes
the consistent-hashing routing observable.

| Query param | Required | Default | Description |
|-------------|----------|---------|-------------|
| `prefix`    | yes      | —       | Prefix to inspect. |
| `mode`      | no       | default | `basic` or `trending`. |

```bash
curl "http://localhost:8000/cache/debug?prefix=iph"
```
```json
{
  "prefix": "iph",
  "mode": "trending",
  "cache_key": "suggest:trending:iph",
  "node": "localhost:6393",
  "node_reachable": true,
  "status": "hit"
}
```

---

## POST /batch/flush

Force the batch writer to flush its buffer to PostgreSQL immediately (handy for
demos/tests so you don't wait for the flush interval).

```bash
curl -X POST http://localhost:8000/batch/flush
```
```json
{ "flushed_distinct_queries": 20 }
```

---

## GET /metrics

Operational metrics: cache hit rate, DB read/write counts, suggestion latency
percentiles, and per-node cache health.

```bash
curl http://localhost:8000/metrics
```
```json
{
  "cache":   { "hits": 6000, "misses": 247, "hit_rate": 0.96,
               "nodes": { "localhost:6391": true, "localhost:6392": true, "localhost:6393": true } },
  "db":      { "reads": 247, "writes": 40 },
  "searches":{ "submissions": 2000, "batch_flushes": 2 },
  "suggest_latency_ms": { "samples": 6247, "p50": 0.76, "p95": 2.65, "p99": 14.98, "max": 38.0 }
}
```

---

## GET /health

Liveness probe; also reports how many queries are loaded.

```json
{ "status": "ok", "queries_loaded": 120000 }
```
