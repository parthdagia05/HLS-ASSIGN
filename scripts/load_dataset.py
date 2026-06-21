"""Load a query,count CSV into the PostgreSQL `queries` table.

Design notes (worth being able to explain):

  * We use PostgreSQL COPY, the fastest way to bulk-load. Loading 120k rows with
    one-INSERT-per-row would take many seconds and thousands of round trips;
    COPY streams them in one go.

  * COPY itself cannot "upsert", so we COPY into a TEMP staging table and then
    do a single  INSERT ... ON CONFLICT DO UPDATE  from staging into `queries`.
    That makes re-running the loader idempotent: it refreshes counts instead of
    erroring on duplicate keys.

  * Every query is normalised (trimmed, lowercased, internal whitespace
    collapsed) so that "IPhone", "iphone " and "iphone" are the same key. This
    matches how the suggestion API normalises the user's prefix.

Run:  python scripts/load_dataset.py [--csv data/queries.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# Make the project root importable so "import app.*" works when this script is
# run directly (python scripts/load_dataset.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


def normalise(query: str) -> str:
    return " ".join(query.split()).lower()


def iter_rows(csv_path: Path):
    """Yield (query, count) tuples from the CSV, skipping malformed lines."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            query = normalise(row.get("query", ""))
            if not query:
                continue
            try:
                count = int(float(row.get("count", "0")))
            except ValueError:
                continue
            yield query, max(0, count)


def load(csv_path: Path) -> int:
    db.init()  # opens the pool and ensures the schema exists
    started = time.perf_counter()

    with db.pool.connection() as conn:
        with conn.cursor() as cur:
            # 1) Stage the raw rows in a temp table (dropped at commit).
            cur.execute(
                "CREATE TEMP TABLE staging (query text, count bigint) "
                "ON COMMIT DROP"
            )
            with cur.copy("COPY staging (query, count) FROM STDIN") as copy:
                for query, count in iter_rows(csv_path):
                    copy.write_row((query, count))

            # 2) Merge staging -> queries. GROUP BY collapses any duplicate
            #    queries in the CSV (keeping the largest count). ON CONFLICT
            #    refreshes counts for queries that already exist.
            cur.execute(
                """
                INSERT INTO queries (query, count)
                SELECT query, max(count) AS count
                FROM staging
                GROUP BY query
                ON CONFLICT (query) DO UPDATE
                    SET count = EXCLUDED.count
                """
            )
        conn.commit()

    total = db.row_count()
    elapsed = time.perf_counter() - started
    print(f"Loaded dataset in {elapsed:.1f}s. queries table now has {total:,} rows.")
    db.close()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Load query dataset into PostgreSQL.")
    parser.add_argument("--csv", default="data/queries.csv",
                        help="path to the CSV file (default data/queries.csv)")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(
            f"CSV not found: {csv_path}\n"
            f"Generate it first:  python scripts/generate_dataset.py"
        )
    load(csv_path)


if __name__ == "__main__":
    main()
