"""Distributed suggestion cache.

This sits in front of PostgreSQL. The suggestion flow is:

    GET /suggest -> check cache -> HIT? return it
                                -> MISS? read PostgreSQL, store in cache, return

The cache is spread across several Redis nodes. We do NOT use Redis Cluster;
instead we route each cache key to a node ourselves using the HashRing. That is
the "distributed cache with consistent hashing" the assignment asks for, and it
keeps the interesting logic in our own (explainable) code.

Resilience: the cache is a best-effort accelerator. If a Redis node is
unreachable, we swallow the error, count it as a miss, and fall back to the
database -- a cache outage must never take the API down.

Cache keys look like:   suggest:<mode>:<prefix>
`mode` is "basic" or "trending" (Phase 4), so the two rankings never collide.
Values are JSON-encoded suggestion lists, stored with a TTL so stale entries
expire on their own even if we never explicitly invalidate them.
"""

from __future__ import annotations

import json

import redis

from app.cache.consistent_hash import HashRing
from app.config import settings
from app.metrics import metrics


class DistributedCache:
    def __init__(self, nodes: list[str], ttl_seconds: int, virtual_nodes: int) -> None:
        self._ttl = ttl_seconds
        self._nodes = list(nodes)
        self._ring = HashRing(nodes, virtual_nodes=virtual_nodes)
        # One Redis client per logical node. Short timeouts so a dead node fails
        # fast and we fall back to the DB instead of hanging the request.
        self._clients: dict[str, redis.Redis] = {}
        for node in nodes:
            host, _, port = node.partition(":")
            self._clients[node] = redis.Redis(
                host=host,
                port=int(port),
                decode_responses=True,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
            )

    # --- key + routing helpers ---
    @staticmethod
    def _key(mode: str, prefix: str) -> str:
        return f"suggest:{mode}:{prefix}"

    def node_for(self, mode: str, prefix: str) -> str:
        """Which logical Redis node owns this (mode, prefix)?"""
        return self._ring.get_node(self._key(mode, prefix))

    # --- cache operations ---
    def get(self, mode: str, prefix: str) -> list[dict] | None:
        """Return the cached suggestion list, or None on a miss / node error.
        Records a hit or miss in metrics either way."""
        key = self._key(mode, prefix)
        client = self._clients[self.node_for(mode, prefix)]
        try:
            raw = client.get(key)
        except redis.RedisError:
            metrics.record_cache_miss()  # treat an unreachable node as a miss
            return None
        if raw is None:
            metrics.record_cache_miss()
            return None
        metrics.record_cache_hit()
        return json.loads(raw)

    def set(self, mode: str, prefix: str, value: list[dict]) -> None:
        """Store a suggestion list with the configured TTL. Best-effort."""
        key = self._key(mode, prefix)
        client = self._clients[self.node_for(mode, prefix)]
        try:
            client.set(key, json.dumps(value), ex=self._ttl)
        except redis.RedisError:
            pass  # cache write failures are non-fatal

    def delete(self, mode: str, prefix: str) -> None:
        """Invalidate one cached prefix (used when rankings change). Best-effort."""
        key = self._key(mode, prefix)
        client = self._clients[self.node_for(mode, prefix)]
        try:
            client.delete(key)
        except redis.RedisError:
            pass

    # --- introspection for GET /cache/debug ---
    def debug(self, mode: str, prefix: str) -> dict:
        """Show which node owns a prefix and whether it is currently cached."""
        key = self._key(mode, prefix)
        node = self.node_for(mode, prefix)
        try:
            present = bool(self._clients[node].exists(key))
            reachable = True
        except redis.RedisError:
            present, reachable = False, False
        return {
            "prefix": prefix,
            "mode": mode,
            "cache_key": key,
            "node": node,
            "node_reachable": reachable,
            "status": "hit" if present else "miss",
        }

    def node_health(self) -> dict[str, bool]:
        """Ping every node. Used by /metrics and demos."""
        health = {}
        for node, client in self._clients.items():
            try:
                health[node] = bool(client.ping())
            except redis.RedisError:
                health[node] = False
        return health

    @property
    def ring(self) -> HashRing:
        return self._ring

    @property
    def nodes(self) -> list[str]:
        return list(self._nodes)


# Shared process-wide cache, configured from settings.
cache = DistributedCache(
    nodes=settings.redis_nodes,
    ttl_seconds=settings.cache_ttl_seconds,
    virtual_nodes=settings.virtual_nodes,
)
