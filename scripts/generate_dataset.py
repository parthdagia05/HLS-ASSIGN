"""Generate a synthetic search-query dataset.

The assignment needs a dataset of at least 100,000 unique queries, each with a
popularity `count`. Real search logs are private, so we synthesise a realistic
one. Two properties make it realistic:

  1. Queries are built from real-world head terms ("iphone", "java", ...) plus
     modifiers ("review", "near me", "2024", ...), so prefixes behave like a
     real typeahead ("ip" -> iphone, iphone 15, ipad, ...).

  2. Counts follow a long-tail (Zipf-like) distribution: a few queries are
     searched millions of times, most are searched rarely. This is exactly the
     shape real search traffic has, and it makes "sort by count" meaningful.

Output: data/queries.csv with a "query,count" header.

Run:  python scripts/generate_dataset.py [--rows 120000] [--out data/queries.csv]

The script is deterministic (fixed random seed) so everyone gets the same data.

NOTE: To use a *real* dataset instead, just produce a CSV with the same
`query,count` header and point load_dataset.py at it -- the loader does not care
how the CSV was produced.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import random
from pathlib import Path

# Deterministic output: same seed -> same dataset every run.
random.seed(42)

# --- Head terms: the "subjects" people search for. ---
HEADS = [
    # Tech / electronics
    "iphone", "ipad", "macbook", "android", "samsung galaxy", "pixel", "laptop",
    "headphones", "earbuds", "smartwatch", "monitor", "keyboard", "mouse",
    "graphics card", "ssd", "router", "webcam", "printer", "tablet", "camera",
    "drone", "speaker", "charger", "power bank", "usb cable", "hard drive",
    # Programming / learning
    "python", "java", "javascript", "typescript", "react", "node", "django",
    "fastapi", "spring boot", "kubernetes", "docker", "postgres", "redis",
    "mongodb", "machine learning", "deep learning", "data science", "sql",
    "system design", "leetcode", "git", "linux", "aws", "azure", "html css",
    # Everyday / shopping
    "running shoes", "office chair", "coffee maker", "air fryer", "backpack",
    "sunglasses", "wallet", "jacket", "sneakers", "water bottle", "yoga mat",
    "blender", "vacuum cleaner", "desk lamp", "standing desk", "mattress",
    # Media / entertainment
    "movies", "tv shows", "music", "podcast", "audiobook", "video game",
    "playstation", "xbox", "nintendo switch", "anime", "documentary",
    # Food / travel / misc
    "pizza", "sushi", "coffee", "restaurants", "hotels", "flights", "car rental",
    "weather", "news", "stock market", "bitcoin", "recipe", "workout",
    "resume template", "cover letter", "interview questions", "online course",
]

# --- Modifiers: words that combine with heads to form longer queries. ---
MODIFIERS = [
    "review", "reviews", "price", "best", "cheap", "buy", "online", "near me",
    "2024", "2025", "deals", "discount", "vs", "alternative", "tutorial",
    "for beginners", "guide", "tips", "specs", "comparison", "pro", "max",
    "mini", "lite", "case", "accessories", "repair", "manual", "setup",
    "free", "download", "course", "certification", "salary", "jobs", "remote",
    "used", "refurbished", "wireless", "bluetooth", "4k", "hd", "portable",
    "gaming", "budget", "premium", "lightweight", "waterproof", "fast",
    "how to use", "where to buy", "is it worth it", "release date",
]


def build_queries(target_rows: int) -> list[str]:
    """Build at least `target_rows` unique query strings.

    We generate in order of decreasing "head-ness": bare heads first (these
    become the most popular), then 2-word combos, then 3-word combos. Insertion
    order is preserved with a dict, and that order later drives the count
    distribution (earlier == more popular)."""
    seen: dict[str, None] = {}

    def add(q: str) -> None:
        q = " ".join(q.split()).lower()  # normalise whitespace + case
        if q:
            seen.setdefault(q, None)

    # 1) Bare head terms -- the most popular queries.
    for head in HEADS:
        add(head)

    # 2) Two-word combinations in both orders ("iphone review", "best iphone").
    for head, mod in itertools.product(HEADS, MODIFIERS):
        add(f"{head} {mod}")
        add(f"{mod} {head}")
        if len(seen) >= target_rows:
            return list(seen.keys())

    # 3) Three-word combinations to top up to the target.
    for head, mod1, mod2 in itertools.product(HEADS, MODIFIERS, MODIFIERS):
        if mod1 == mod2:
            continue
        add(f"{head} {mod1} {mod2}")
        if len(seen) >= target_rows:
            break

    return list(seen.keys())


def assign_count(rank: int) -> int:
    """Zipf-like count for a query at position `rank` (0 = most popular).

    count ~ C / (rank + 1)^s, with multiplicative jitter so ties are broken
    naturally and the data does not look perfectly synthetic."""
    base = 5_000_000.0
    s = 0.92
    raw = base / ((rank + 1) ** s)
    jitter = random.uniform(0.7, 1.3)
    return max(1, int(raw * jitter))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a typeahead dataset.")
    parser.add_argument("--rows", type=int, default=120_000,
                        help="number of unique queries to generate (default 120000)")
    parser.add_argument("--out", default="data/queries.csv",
                        help="output CSV path (default data/queries.csv)")
    args = parser.parse_args()

    queries = build_queries(args.rows)
    print(f"Generated {len(queries):,} unique queries.")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["query", "count"])
        for rank, query in enumerate(queries):
            writer.writerow([query, assign_count(rank)])

    print(f"Wrote {out_path} ({out_path.stat().st_size / 1_000_000:.1f} MB).")
    print("Next: python scripts/load_dataset.py")


if __name__ == "__main__":
    main()
