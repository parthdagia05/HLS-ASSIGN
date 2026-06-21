# Search Typeahead System

A backend-focused search typeahead (autocomplete) system, similar to the
suggestion box in Google / Amazon / YouTube. It suggests popular queries as you
type, records searches, serves suggestions from a **distributed cache** (Redis
nodes routed with **consistent hashing**), ranks results with a
**recency-aware "trending"** score, and absorbs write pressure with
**batched writes**.

> Built for the HLD101 "Build a Search Typeahead System" assignment.
> Full architecture, API docs, and performance report are in [`docs/`](docs/).

## Tech stack

| Layer        | Choice                          | Why |
|--------------|---------------------------------|-----|
| Backend      | Python 3.12 + FastAPI           | Minimal boilerplate; auto OpenAPI docs |
| Primary store| PostgreSQL 16                   | Reliable counts; indexed prefix lookups |
| Cache        | Redis (3 logical nodes)         | Low-latency reads; we route with our own consistent hashing |
| Frontend     | Plain HTML/CSS/JS               | Zero build step; easy to read |

## Quick start

```bash
# 1. Start infrastructure (PostgreSQL + 3 Redis cache nodes)
docker compose up -d

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Generate + load the dataset (>= 100k queries)   [added in Phase 1]
python scripts/generate_dataset.py
python scripts/load_dataset.py

# 4. Run the API
uvicorn app.main:app --reload

# 5. Open the UI
#    http://localhost:8000/        (frontend, added in Phase 2)
#    http://localhost:8000/docs    (interactive API docs)
```

## Project layout

```
search-typeahead/
├── app/                 # FastAPI backend
│   ├── config.py        # all tunables, read from env (.env.example)
│   ├── db.py            # PostgreSQL pool + schema + SQL helpers
│   ├── main.py          # API entry point and routes
│   ├── cache/           # distributed cache + consistent hashing
│   └── services/        # suggestion / trending / batch-write logic
├── scripts/             # dataset generation + loading + benchmarks
├── frontend/            # search UI
├── docs/                # architecture, API, performance report
├── docker-compose.yml   # PostgreSQL + 3 Redis nodes
└── requirements.txt
```

## Build phases

This repository is built in clean, incremental commits:

1. **Phase 1** — dataset ingestion + basic prefix suggestion API
2. **Phase 2** — frontend search UI (typeahead dropdown, keyboard nav)
3. **Phase 3** — distributed Redis cache with consistent hashing
4. **Phase 4** — trending searches with recency-aware ranking
5. **Phase 5** — batch writes for search-count updates
6. **Phase 6** — performance report + documentation
