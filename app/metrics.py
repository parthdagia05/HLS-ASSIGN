"""In-process metrics: cache hit rate, DB read/write counts, and suggestion
latency percentiles.

The assignment asks us to *report* cache hit rate, DB read/write counts, and
p95 latency. We collect those numbers here and expose them via GET /metrics.

Everything is guarded by a lock because requests are served on multiple worker
threads (FastAPI runs sync endpoints in a threadpool) and the batch writer runs
on its own thread, so counters are touched concurrently.
"""

from __future__ import annotations

import math
import threading
from collections import deque


class Metrics:
    def __init__(self, latency_window: int = 10_000) -> None:
        self._lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_reads = 0            # times we queried PostgreSQL for suggestions
        self.db_writes = 0           # rows written to PostgreSQL (naive or batched)
        self.search_submissions = 0  # POST /search calls received
        self.batch_flushes = 0       # number of batch-writer flushes (Phase 5)
        # Keep only the most recent N latency samples so memory stays bounded.
        self._latencies_ms: deque[float] = deque(maxlen=latency_window)

    # --- recording (called from hot paths) ---
    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self.cache_misses += 1

    def record_db_read(self, n: int = 1) -> None:
        with self._lock:
            self.db_reads += n

    def record_db_write(self, n: int = 1) -> None:
        with self._lock:
            self.db_writes += n

    def record_search_submission(self, n: int = 1) -> None:
        with self._lock:
            self.search_submissions += n

    def record_batch_flush(self) -> None:
        with self._lock:
            self.batch_flushes += 1

    def record_suggest_latency(self, ms: float) -> None:
        with self._lock:
            self._latencies_ms.append(ms)

    # --- reporting ---
    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        """Linear-interpolation percentile. `p` is a fraction, e.g. 0.95."""
        if not values:
            return 0.0
        k = (len(values) - 1) * p
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return values[int(k)]
        return values[lo] * (hi - k) + values[hi] * (k - lo)

    def snapshot(self) -> dict:
        """A consistent point-in-time view of all metrics."""
        with self._lock:
            hits, misses = self.cache_hits, self.cache_misses
            latencies = sorted(self._latencies_ms)
            total_lookups = hits + misses
            return {
                "cache": {
                    "hits": hits,
                    "misses": misses,
                    "hit_rate": round(hits / total_lookups, 4) if total_lookups else 0.0,
                },
                "db": {
                    "reads": self.db_reads,
                    "writes": self.db_writes,
                },
                "searches": {
                    "submissions": self.search_submissions,
                    "batch_flushes": self.batch_flushes,
                },
                "suggest_latency_ms": {
                    "samples": len(latencies),
                    "p50": round(self._percentile(latencies, 0.50), 3),
                    "p95": round(self._percentile(latencies, 0.95), 3),
                    "p99": round(self._percentile(latencies, 0.99), 3),
                    "max": round(latencies[-1], 3) if latencies else 0.0,
                },
            }

    def reset(self) -> None:
        """Zero everything. Used by the benchmark script for a clean run."""
        with self._lock:
            self.cache_hits = self.cache_misses = 0
            self.db_reads = self.db_writes = 0
            self.search_submissions = self.batch_flushes = 0
            self._latencies_ms.clear()


# Shared process-wide instance.
metrics = Metrics()
