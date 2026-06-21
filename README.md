# Search Typeahead System

A backend-focused search typeahead (autocomplete) system, similar to the
suggestion box in Google / Amazon / YouTube. It suggests popular queries as you
type, records searches, serves suggestions from a **distributed cache** (Redis
nodes routed with **consistent hashing**), ranks results with a
**recency-aware "trending"** score, and absorbs write pressure with
**batched writes**.

> HLD101 "Build a Search Typeahead System" assignment.

| Highlight | Measured (local, 120k queries) |
|-----------|-------------------------------|
| Suggestion latency (server p95, warm) | **2.65 ms** |
| Cache hit rate | **96%** |
| Write reduction via batching | **≈50×** |
| Consistent-hashing remap on node loss | **~1/N** (vs ~100% for `hash % N`) |

See the full **[performance report](docs/performance.md)** and
**[architecture](docs/architecture.md)**.

## Screenshots

| Typeahead dropdown | Search result + trending |
|--------------------|--------------------------|
| ![typeahead](docs/screenshots/typeahead.png) | ![search result](docs/screenshots/search-result.png) |

## Tech stack

| Layer        | Choice                          | Why |
|--------------|---------------------------------|-----|
| Backend      | Python 3.12 + FastAPI           | Minimal boilerplate; auto OpenAPI docs at `/docs` |
| Primary store| PostgreSQL 16                   | Reliable counts; indexed prefix lookups; batched upserts |
| Cache        | Redis (3 logical nodes)         | Low-latency reads; routed with our own consistent hashing |
| Frontend     | Plain HTML/CSS/JS               | Zero build step; easy to read |

## Quick start

Prerequisites: Docker (+ Compose) and Python 3.12.

```bash
# 1. Start infrastructure: PostgreSQL + 3 Redis cache nodes
docker compose up -d

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Generate the dataset (>= 100k queries) and load it into PostgreSQL
python scripts/generate_dataset.py
python scripts/load_dataset.py

# 4. Run the API (serves the UI too)
uvicorn app.main:app --reload

# 5. Open it
#    http://localhost:8000/        the search UI
#    http://localhost:8000/docs    interactive API documentation
```

To stop the infrastructure: `docker compose down` (add `-v` to wipe the DB).

## Dataset

The system needs a dataset of `query,count` rows (≥ 100,000 unique queries).

* **Default (synthetic, reproducible):** `scripts/generate_dataset.py` builds
  120,000 unique queries from real-world head terms + modifiers, with a
  Zipf-like (long-tail) count distribution — the shape real search traffic has.
  It is deterministic (fixed seed), so everyone gets the same data and no
  download is required. Output: `data/queries.csv`.
* **Using a real dataset instead:** produce a CSV with a `query,count` header
  (any open dataset of search queries / product names / page titles works; if it
  has no counts, aggregate to derive them) and point the loader at it:
  `python scripts/load_dataset.py --csv path/to/your.csv`.

`load_dataset.py` bulk-loads via PostgreSQL `COPY` into a staging table, then
upserts into `queries`, so it is fast and safe to re-run.

## API

| API | Method | Purpose |
|-----|--------|---------|
| `/suggest?q=<prefix>&mode=` | GET | Top-10 prefix suggestions (`basic` or `trending` ranking) |
| `/search` | POST | Submit a search → `{"message":"Searched"}`, recorded via batch writer |
| `/trending?limit=` | GET | Currently trending searches |
| `/cache/debug?prefix=` | GET | Which cache node owns a prefix + hit/miss |
| `/metrics` | GET | Cache hit rate, DB read/write counts, latency percentiles |
| `/batch/flush` | POST | Force a batch flush (for demos) |
| `/health` | GET | Liveness + queries loaded |

Full details with examples: **[docs/api.md](docs/api.md)**.

## Demo / verification scripts

Run these with the server up and the dataset loaded:

```bash
python scripts/cache_demo.py      # consistent hashing: even spread + ~1/N remap
python scripts/trending_demo.py   # basic vs trending ranking on a live burst
python scripts/batch_demo.py      # ~50x write reduction from batching
python scripts/benchmark.py       # latency percentiles + cache hit rate
```

## How it works (one-paragraph tour)

`GET /suggest` normalises the prefix, checks the **distributed cache** (a Redis
node chosen by a **consistent-hash ring** with virtual nodes), and on a miss
reads PostgreSQL (`WHERE query LIKE 'prefix%' ORDER BY ...`) and populates the
cache. Ranking is either **basic** (all-time `count`) or **trending**
(`count + α · decayed_recent_score`); the recent score decays exponentially, so
short-lived spikes fade instead of ranking forever. `POST /search` does **not**
write to the database directly — it hands the query to the **batch writer**,
which buffers and aggregates submissions and flushes them in a single multi-row
upsert (with a write-ahead log for crash recovery). Read the full story in
**[docs/architecture.md](docs/architecture.md)**.

## Project layout

```
search-typeahead/
├── app/                       # FastAPI backend
│   ├── config.py              # all tunables, read from env (.env.example)
│   ├── db.py                  # PostgreSQL pool + schema + SQL (suggestions, batch upsert)
│   ├── metrics.py             # hit rate, DB r/w counts, latency percentiles
│   ├── models.py              # Pydantic request/response models
│   ├── textutil.py            # query normalisation + safe LIKE escaping
│   ├── main.py                # API routes + app lifecycle
│   ├── cache/
│   │   ├── consistent_hash.py # the hash ring (virtual nodes)
│   │   └── distributed_cache.py
│   └── services/
│       ├── suggestions.py     # cache-first suggestion read path
│       └── batch_writer.py    # buffer + aggregate + WAL + flush
├── scripts/                   # dataset gen/load + demos + benchmark
├── frontend/                  # search UI (index.html, style.css, app.js)
├── docs/                      # architecture, API, performance, screenshots
├── docker-compose.yml         # PostgreSQL + 3 Redis nodes
└── requirements.txt
```

## Configuration

Every tunable lives in `app/config.py` and can be overridden via environment
variables (see `.env.example`): cache TTL and virtual-node count, suggestion
limit and max cached prefix length, the trending weight (`RECENCY_ALPHA`) and
half-life, and the batch size / flush interval / WAL toggle.

## Build history

The repository is built in clean, incremental commits — one per phase:

1. `chore: scaffold` — infra, config, DB pool
2. `feat: load dataset and implement basic prefix suggestion API`
3. `feat: add search UI with typeahead dropdown`
4. `feat: add distributed Redis cache with consistent hashing`
5. `feat: implement trending search with recency-aware ranking`
6. `feat: implement batch writes for search-count updates`
7. `docs: add performance report, architecture, API docs, screenshots`
