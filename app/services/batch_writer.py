"""Batch writer for search-count updates.

PROBLEM: writing to PostgreSQL synchronously on every POST /search is wasteful.
A popular query searched 1,000 times in a few seconds would cause 1,000 separate
UPDATEs of the same row.

SOLUTION: buffer submissions in memory, AGGREGATE repeated queries, and flush the
whole buffer to the database in a single statement either
  * every `flush_interval_seconds` (time trigger), or
  * once `batch_size` distinct queries are buffered (size trigger),
whichever happens first. So 1,000 searches of one query become +1000 in a single
write. This is the write-reduction the assignment asks us to demonstrate.

DURABILITY / FAILURE TRADE-OFF: the buffer lives in memory, so a crash between
flushes would lose those counts. To bound that loss we keep an optional
write-ahead log (WAL): every submission is appended to a file first. On flush we
"rotate" the WAL (atomic rename) and only delete the rotated file once the
database write succeeds. On startup we replay any leftover WAL files. Caveats,
spelled out in docs/architecture.md:
  * we flush to the OS but do not fsync per write, so a power/OS crash (not just
    a process crash) can still lose the last few entries -- a deliberate
    throughput trade-off;
  * recovery is at-least-once: a crash in the small window after the DB commit
    but before deleting the rotated WAL replays that batch again (slight
    over-count). Exactly-once would need the counts to be idempotent.
With the WAL disabled (BATCH_WAL_ENABLED=false) the writer is pure in-memory:
faster, but a crash loses the un-flushed buffer entirely.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from pathlib import Path

from app import db
from app.cache.distributed_cache import cache
from app.config import settings
from app.metrics import metrics


class BatchWriter:
    def __init__(self) -> None:
        self._buffer: Counter[str] = Counter()  # query -> pending count delta
        self._lock = threading.Lock()           # guards _buffer and the WAL handle
        self._flush_lock = threading.Lock()      # serialises whole flushes
        self._wake = threading.Event()           # set to wake the flush thread early
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._wal_enabled = settings.batch_wal_enabled
        self._wal_path = Path(settings.batch_wal_path)
        self._flushing_path = self._wal_path.with_suffix(self._wal_path.suffix + ".flushing")
        self._wal_fh = None

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        """Recover any un-flushed WAL, then start the background flush thread."""
        if self._wal_enabled:
            self._wal_path.parent.mkdir(parents=True, exist_ok=True)
            self._recover()
            self._wal_fh = open(self._wal_path, "a", encoding="utf-8")
        self._thread = threading.Thread(target=self._run, name="batch-writer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread and flush whatever remains (clean shutdown loses nothing)."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.flush()  # final drain
        if self._wal_fh:
            self._wal_fh.close()
            self._wal_fh = None

    # -- submission ----------------------------------------------------------
    def submit(self, query: str, n: int = 1) -> None:
        """Record `n` searches of `query`. Cheap: a dict bump plus an optional
        file append -- never a database round trip."""
        with self._lock:
            self._buffer[query] += n
            if self._wal_fh:
                self._wal_fh.write(f"{query}\n" * n)
                self._wal_fh.flush()  # hand off to the OS (survives a process crash)
            buffered = len(self._buffer)
        if buffered >= settings.batch_size:
            self._wake.set()  # size trigger: ask the thread to flush now

    # -- flushing ------------------------------------------------------------
    def _run(self) -> None:
        """Background loop: flush on a timer or when woken by the size trigger."""
        while not self._stop.is_set():
            # Wait up to flush_interval, but wake immediately if signalled.
            self._wake.wait(timeout=settings.flush_interval_seconds)
            self._wake.clear()
            self.flush()

    def flush(self) -> int:
        """Write the buffered, aggregated counts to PostgreSQL in one statement
        and invalidate the affected cache entries. Returns rows written."""
        with self._flush_lock:
            # 1) Atomically take the current buffer and rotate the WAL, so new
            #    submissions during the DB write accumulate in a fresh buffer/WAL.
            with self._lock:
                if not self._buffer:
                    return 0
                pending = dict(self._buffer)
                self._buffer = Counter()
                if self._wal_fh:
                    self._wal_fh.close()
                    os.replace(self._wal_path, self._flushing_path)  # atomic rename
                    self._wal_fh = open(self._wal_path, "a", encoding="utf-8")

            # 2) Single batched write + cache invalidation (outside the lock).
            db.apply_batch(pending)
            for query in pending:
                cache.invalidate_query(query)

            # 3) The batch is durably in the DB now; drop the rotated WAL.
            if self._wal_enabled and self._flushing_path.exists():
                self._flushing_path.unlink()

            metrics.record_batch_flush()
            return len(pending)

    # -- recovery ------------------------------------------------------------
    def _recover(self) -> None:
        """Replay leftover WAL files from a previous crash into the database."""
        counts: Counter[str] = Counter()
        for path in (self._flushing_path, self._wal_path):  # older rotated first
            if path.exists():
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        query = line.strip()
                        if query:
                            counts[query] += 1
        if counts:
            db.apply_batch(dict(counts))
            print(f"[batch-writer] recovered {sum(counts.values())} searches "
                  f"({len(counts)} distinct) from WAL")
        # Reset the WAL files to a clean state.
        for path in (self._flushing_path, self._wal_path):
            if path.exists():
                path.unlink()


# Shared process-wide instance.
batch_writer = BatchWriter()
