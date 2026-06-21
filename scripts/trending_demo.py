"""Demonstrate the difference between BASIC and TRENDING ranking.

Story: pick a normally-unpopular query, submit it many times in a short burst,
then compare the two rankings for its prefix:

  * basic    -> ranking barely changes (count is huge for the head queries;
                a few hundred extra searches do not move the tail much).
  * trending -> the freshly-searched query jumps toward the top, because its
                decayed recent_score is now large.

This is the "demonstrate the difference using sample data or logs" deliverable.

Prerequisites: the API is running (uvicorn app.main:app) and the dataset is
loaded.

Run:  python scripts/trending_demo.py
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"
PREFIX = "java t"
TARGET = "java tutorial for beginners"  # a low-count tail query under that prefix
BURST = 400                             # how many times we "search" the target


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}") as resp:
        return json.loads(resp.read())


def post_search(query: str) -> None:
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        f"{BASE}/search", data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req).read()


def ranking(prefix: str, mode: str) -> list[str]:
    data = get_json(f"/suggest?q={urllib.parse.quote(prefix)}&mode={mode}")
    return [s["query"] for s in data["suggestions"]]


def rank_of(query: str, ordered: list[str]) -> str:
    return str(ordered.index(query) + 1) if query in ordered else "not in top 10"


def show(title: str, ordered: list[str]) -> None:
    print(f"\n{title}")
    for i, q in enumerate(ordered, 1):
        marker = "  <-- target" if q == TARGET else ""
        print(f"   {i:>2}. {q}{marker}")


def main() -> None:
    try:
        get_json("/health")
    except Exception:
        sys.exit("API is not reachable at http://localhost:8000 -- start it first.")

    print(f"Target query : {TARGET!r}")
    print(f"Prefix       : {PREFIX!r}")
    print(f"Burst        : {BURST} searches")

    basic_before = ranking(PREFIX, "basic")
    trending_before = ranking(PREFIX, "trending")
    show("BASIC ranking (before burst)", basic_before)
    print(f"   target rank: {rank_of(TARGET, basic_before)}")

    print(f"\nSubmitting {BURST} searches for {TARGET!r} ...")
    for _ in range(BURST):
        post_search(TARGET)

    basic_after = ranking(PREFIX, "basic")
    trending_after = ranking(PREFIX, "trending")

    show("BASIC ranking (after burst) -- little change", basic_after)
    print(f"   target rank: {rank_of(TARGET, basic_after)}")

    show("TRENDING ranking (after burst) -- target surges", trending_after)
    print(f"   target rank: {rank_of(TARGET, trending_after)}")

    print(
        "\nConclusion: the same /suggest API, switched via ?mode=, ranks the "
        "recently-hot query far higher under 'trending' while 'basic' stays "
        "anchored to all-time popularity. The trending boost will decay back "
        f"toward the basic order over ~{1} half-life(s) if the burst stops."
    )


if __name__ == "__main__":
    main()
