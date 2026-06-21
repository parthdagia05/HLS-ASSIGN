"""Consistent hashing ring.

WHY consistent hashing (and not `hash(key) % num_nodes`)?
    With plain modulo, adding or removing a cache node changes the result of the
    modulo for almost every key, so nearly the whole cache is invalidated at
    once. Consistent hashing places both nodes and keys on a circular hash space
    ("the ring"); a key belongs to the first node found clockwise from it. When a
    node is added or removed, only the keys in that node's arc move -- on average
    just 1/N of the keys.

VIRTUAL NODES:
    If each physical node sat at a single point on the ring, the arcs would be
    very uneven and load would be lopsided. So we place each physical node at
    many points (`virtual_nodes` copies). More points -> smoother, more uniform
    distribution of keys across the physical nodes.

This class is pure logic with no Redis dependency, which makes it trivial to
unit test and to explain in isolation.
"""

from __future__ import annotations

import bisect
import hashlib


class HashRing:
    def __init__(self, nodes: list[str], virtual_nodes: int = 150) -> None:
        self._virtual_nodes = virtual_nodes
        self._ring: dict[int, str] = {}   # hash point -> physical node name
        self._sorted_points: list[int] = []  # sorted hash points for binary search
        for node in nodes:
            self.add_node(node)

    @staticmethod
    def _hash(key: str) -> int:
        """Map a string to a point on the ring (a 128-bit integer from MD5).

        MD5 is used purely as a fast, well-distributed hash here -- not for any
        security purpose, so it is an appropriate choice."""
        return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)

    def add_node(self, node: str) -> None:
        """Place `virtual_nodes` copies of `node` onto the ring."""
        for i in range(self._virtual_nodes):
            point = self._hash(f"{node}#{i}")
            self._ring[point] = node
            bisect.insort(self._sorted_points, point)

    def remove_node(self, node: str) -> None:
        """Remove all of `node`'s virtual points from the ring."""
        for i in range(self._virtual_nodes):
            point = self._hash(f"{node}#{i}")
            if point in self._ring:
                del self._ring[point]
                idx = bisect.bisect_left(self._sorted_points, point)
                if idx < len(self._sorted_points) and self._sorted_points[idx] == point:
                    self._sorted_points.pop(idx)

    def get_node(self, key: str) -> str:
        """Return the physical node responsible for `key`.

        We hash the key, then walk clockwise to the first ring point at or after
        it (binary search), wrapping around to the start if we run off the end.
        """
        if not self._sorted_points:
            raise RuntimeError("hash ring is empty -- no cache nodes configured")
        point = self._hash(key)
        idx = bisect.bisect(self._sorted_points, point)
        if idx == len(self._sorted_points):
            idx = 0  # wrap around the circle
        return self._ring[self._sorted_points[idx]]

    def distribution(self, keys: list[str]) -> dict[str, int]:
        """Helper for demos: count how many of `keys` land on each node.

        Used by the cache-distribution demo to *show* that consistent hashing
        spreads load roughly evenly."""
        counts: dict[str, int] = {}
        for key in keys:
            node = self.get_node(key)
            counts[node] = counts.get(node, 0) + 1
        return counts
