"""
AURA In-Memory Cache Tests
============================
Tests for InMemoryCache get/set/delete/clear/TTL expiry and eviction.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.cache import InMemoryCache

# ── Basic get/set ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_and_get():
    cache = InMemoryCache(default_ttl=60)
    await cache.set("k1", "v1")
    assert await cache.get("k1") == "v1"


@pytest.mark.asyncio
async def test_get_missing_key():
    cache = InMemoryCache()
    assert await cache.get("nonexistent") is None


# ── TTL expiry ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_expired_key():
    cache = InMemoryCache(default_ttl=60)
    await cache.set("k1", "v1", ttl=0)
    # Immediately expired (ttl=0 means expires_at = monotonic() + 0)
    await asyncio.sleep(0.01)
    assert await cache.get("k1") is None


@pytest.mark.asyncio
async def test_custom_ttl():
    cache = InMemoryCache(default_ttl=1)
    await cache.set("k1", "v1", ttl=60)
    assert await cache.get("k1") == "v1"


# ── Delete ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete():
    cache = InMemoryCache()
    await cache.set("k1", "v1")
    await cache.delete("k1")
    assert await cache.get("k1") is None


@pytest.mark.asyncio
async def test_delete_nonexistent():
    cache = InMemoryCache()
    await cache.delete("nope")  # should not raise


# ── Clear ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear():
    cache = InMemoryCache()
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.clear()
    assert cache.size == 0


# ── Clear prefix ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_prefix():
    cache = InMemoryCache()
    await cache.set("schema:users", 1)
    await cache.set("schema:orders", 2)
    await cache.set("query:abc", 3)
    await cache.clear_prefix("schema:")
    assert await cache.get("schema:users") is None
    assert await cache.get("schema:orders") is None
    assert await cache.get("query:abc") == 3


# ── Size property ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_size():
    cache = InMemoryCache()
    assert cache.size == 0
    await cache.set("a", 1)
    await cache.set("b", 2)
    assert cache.size == 2


# ── Eviction trigger ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_eviction_triggered_after_100_sets():
    cache = InMemoryCache(default_ttl=0)
    # Set 100 items with ttl=0 (they expire immediately)
    for i in range(100):
        await cache.set(f"key_{i}", i, ttl=0)

    await asyncio.sleep(0.01)
    # The 100th set triggers _evict_expired_locked, clearing expired entries
    # After eviction counter resets, size should be reduced
    # Add one more to trigger eviction
    await cache.set("trigger", "val", ttl=0)
    await asyncio.sleep(0.01)

    # Manually verify eviction ran (counter was reset to 0)
    assert cache._evict_counter < 100


# ── Overwrite existing key ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_overwrite_key():
    cache = InMemoryCache()
    await cache.set("k", "old")
    await cache.set("k", "new")
    assert await cache.get("k") == "new"
