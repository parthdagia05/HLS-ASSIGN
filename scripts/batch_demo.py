"""Demonstrate that batch writes drastically reduce database writes.

We submit many searches spread over a small set of distinct queries (lots of
repeats, like real traffic), force a flush, then read /metrics and compare:

    submissions  (POST /search calls)   vs   db writes  (rows actually written)

With the naive one-write-per-search approach these would be equal. With batching
+ aggregation, db writes collapse to roughly the number of DISTINCT queries per
flush -- a large reduction.

Prerequisites: API running, dataset loaded.

Run:  python scripts/batch_demo.py
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://localhost:8000"
DISTINCT_QUERIES = [f"trending demo query {i}" for i in range(20)]
SEARCHES = 2000  # total POST /search calls, spread over the 20 distinct queries


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}") as resp:
        return json.loads(resp.read())


def post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main() -> None:
    try:
        get_json("/health")
    except Exception:
        sys.exit("API is not reachable at http://localhost:8000 -- start it first.")

    before = get_json("/metrics")
    sub0 = before["searches"]["submissions"]
    wr0 = before["db"]["writes"]

    print(f"Submitting {SEARCHES} searches across {len(DISTINCT_QUERIES)} distinct queries...")
    for i in range(SEARCHES):
        post("/search", {"query": DISTINCT_QUERIES[i % len(DISTINCT_QUERIES)]})

    flushed = post("/batch/flush")["flushed_distinct_queries"]
    print(f"Forced a final flush: {flushed} distinct queries written in that flush.\n")

    after = get_json("/metrics")
    submissions = after["searches"]["submissions"] - sub0
    db_writes = after["db"]["writes"] - wr0
    flushes = after["searches"]["batch_flushes"] - before["searches"]["batch_flushes"]

    print("Results")
    print(f"   search submissions : {submissions}")
    print(f"   DB row writes       : {db_writes}")
    print(f"   batch flushes       : {flushes}")
    if db_writes:
        print(f"\n   naive approach would have done ~{submissions} DB writes.")
        print(f"   batching did {db_writes}  ->  "
              f"{(1 - db_writes / submissions) * 100:.1f}% fewer writes "
              f"({submissions / db_writes:.1f}x reduction).")


if __name__ == "__main__":
    main()
