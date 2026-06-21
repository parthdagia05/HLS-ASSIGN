"""Benchmark the suggestion API: latency percentiles, cache hit rate, DB reads.

It drives GET /suggest under concurrent load using a realistic mix of prefixes
(many repeats, so the cache gets exercised the way it would in production), then
prints a report combining client-measured latency with the server's own
/metrics counters.

Run it against a FRESHLY STARTED server (so /metrics starts from zero):
    uvicorn app.main:app --port 8000
    python scripts/benchmark.py

Two phases:
  * cold  -- each distinct prefix requested once (mostly cache misses -> DB)
  * warm  -- many requests sampled from the same prefixes (mostly cache hits)
The latency difference between them shows the cache's effect directly.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_dataset import HEADS  # reuse the head terms for realistic prefixes

BASE = "http://localhost:8000"
CONCURRENCY = 12
WARM_REQUESTS = 6000


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}") as resp:
        return json.loads(resp.read())


def time_suggest(prefix: str) -> float:
    """Issue one /suggest request, return client round-trip time in ms."""
    url = f"{BASE}/suggest?q={urllib.parse.quote(prefix)}"
    start = time.perf_counter()
    with urllib.request.urlopen(url) as resp:
        resp.read()
    return (time.perf_counter() - start) * 1000.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def build_prefixes() -> list[str]:
    """Distinct prefixes of length 2-4 taken from the dataset's head terms."""
    prefixes = set()
    for head in HEADS:
        for k in (2, 3, 4):
            if len(head) >= k:
                prefixes.add(head[:k])
    return sorted(prefixes)


def run_phase(name: str, prefixes: list[str]) -> list[float]:
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        latencies = list(pool.map(time_suggest, prefixes))
    p50, p95, p99 = (percentile(latencies, p) for p in (0.50, 0.95, 0.99))
    print(f"\n{name}  ({len(prefixes)} requests, concurrency {CONCURRENCY})")
    print(f"   client latency  p50={p50:.2f}ms  p95={p95:.2f}ms  p99={p99:.2f}ms")
    return latencies


def main() -> None:
    try:
        get_json("/health")
    except Exception:
        sys.exit("API is not reachable at http://localhost:8000 -- start it first.")

    prefixes = build_prefixes()
    print(f"Distinct prefixes: {len(prefixes)}")

    # Cold: each distinct prefix once -> first touch is a cache miss.
    run_phase("COLD (cache empty -> DB)", prefixes)

    # Warm: many requests sampled (with repeats) from the same prefixes.
    warm_load = [prefixes[i % len(prefixes)] for i in range(WARM_REQUESTS)]
    start = time.perf_counter()
    run_phase("WARM (cache populated)", warm_load)
    wall = time.perf_counter() - start
    print(f"   throughput      {len(warm_load) / wall:,.0f} req/s")

    # Server-side truth from /metrics.
    m = get_json("/metrics")
    print("\nServer /metrics")
    print(f"   cache hit rate  {m['cache']['hit_rate'] * 100:.1f}%  "
          f"(hits={m['cache']['hits']}, misses={m['cache']['misses']})")
    print(f"   DB reads        {m['db']['reads']}")
    lat = m["suggest_latency_ms"]
    print(f"   server latency  p50={lat['p50']}ms  p95={lat['p95']}ms  "
          f"p99={lat['p99']}ms  (samples={lat['samples']})")
    print(f"   cache nodes     {m['cache']['nodes']}")


if __name__ == "__main__":
    main()
