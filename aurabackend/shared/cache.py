"""
In-Memory TTL Cache
====================
Thread-safe, asyncio-native TTL cache with lazy eviction.
No external dependencies — Redis can be layered on top later
by swapping the backend behind the same interface.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float  # monotonic clock seconds


class InMemoryCache:
    """
    Async-safe TTL cache.

    Usage::

        cache = InMemoryCache(default_ttl=300)
        await cache.set("key", value)
        hit = await cache.get("key")          # None if expired/missing
        await cache.delete("key")
        await cache.clear_prefix("schema:")   # bulk invalidate
    """

    def __init__(self, default_ttl: int = 60) -> None:
        self._store: Dict[str, _CacheEntry] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
        self._evict_counter = 0

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.monotonic() + ttl
        async with self._lock:
            self._store[key] = _CacheEntry(value=value, expires_at=expires_at)
            self._evict_counter += 1
            if self._evict_counter >= 100:
                await self._evict_expired_locked()
                self._evict_counter = 0

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear_prefix(self, prefix: str) -> None:
        async with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    async def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]

    @property
    def size(self) -> int:
        return len(self._store)


# ── Module-level singletons ────────────────────────────────────────────

# Hot path: dashboard stats (30s TTL — matches frontend polling interval)
dashboard_cache = InMemoryCache(default_ttl=30)

# Schema introspection results (10 min TTL — schemas rarely change)
schema_cache = InMemoryCache(default_ttl=600)

# Recent query results (5 min TTL — useful for re-runs from history)
query_cache = InMemoryCache(default_ttl=300)

# Health check aggregation (15s TTL)
health_cache = InMemoryCache(default_ttl=15)
