"""Demonstrate the two key properties of consistent hashing, with numbers.

Run:  python scripts/cache_demo.py

It prints:
  1. How a large set of prefixes distributes across the cache nodes
     (should be roughly even thanks to virtual nodes).
  2. How few keys move when a node is removed
     (should be ~1/N, NOT ~all of them as with hash % N).

This is the "logs/explanation showing consistent-hashing behavior" the
assignment asks for, and it runs without Redis -- it exercises the ring logic
directly.
"""

from __future__ import annotations

import itertools
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cache.consistent_hash import HashRing  # noqa: E402
from app.config import settings  # noqa: E402


def sample_prefixes(n: int = 20_000) -> list[str]:
    """Generate n distinct cache keys that look like real suggestion keys."""
    letters = string.ascii_lowercase
    combos = itertools.product(letters, repeat=3)  # 'aaa'..'zzz' = 17,576
    keys = [f"suggest:basic:{''.join(c)}" for c in combos]
    # Top up with 4-letter combos if we need more than 17,576.
    if len(keys) < n:
        for c in itertools.product(letters, repeat=4):
            keys.append(f"suggest:basic:{''.join(c)}")
            if len(keys) >= n:
                break
    return keys[:n]


def main() -> None:
    nodes = settings.redis_nodes
    keys = sample_prefixes()
    print(f"Nodes: {nodes}")
    print(f"Virtual nodes per node: {settings.virtual_nodes}")
    print(f"Sample keys: {len(keys):,}\n")

    # --- Property 1: even distribution ---
    ring = HashRing(nodes, virtual_nodes=settings.virtual_nodes)
    dist = ring.distribution(keys)
    print("1) Key distribution across nodes")
    ideal = len(keys) / len(nodes)
    for node in nodes:
        count = dist.get(node, 0)
        skew = (count - ideal) / ideal * 100
        print(f"   {node}: {count:>6,} keys ({skew:+.1f}% vs even split)")

    # --- Property 2: minimal remapping when a node leaves ---
    before = {key: ring.get_node(key) for key in keys}
    removed = nodes[-1]
    ring.remove_node(removed)
    moved = sum(1 for key in keys if before[key] != ring.get_node(key))
    print(f"\n2) Removing node {removed}")
    print(f"   keys remapped: {moved:,} / {len(keys):,} "
          f"({moved / len(keys) * 100:.1f}%)")
    print(f"   (consistent hashing target ~= 1/{len(nodes)} "
          f"= {100 / len(nodes):.1f}%; plain hash % N would move ~100%)")


if __name__ == "__main__":
    main()
