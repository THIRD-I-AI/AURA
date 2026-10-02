"""BUG-313: with Redis configured but unreachable, every throttled request raised inside
the rate-limit middleware (HTTP 500 on every route) and the documented fallback could
never trigger. BUG-307: the Redis URL, password included, was written to the log.
BUG-317: the in-memory limiter kept a key for every client ever seen."""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from shared import rate_limit
from shared.rate_limit import InMemoryBackend, RedisBackend, _redact_url

pytest.importorskip("redis")

# Nothing listens on port 1: a connection attempt is refused straight away.
DEAD_REDIS = "redis://default:S3cretPw@127.0.0.1:1/0"


def test_an_unreachable_redis_does_not_fail_the_request():
    backend = RedisBackend(DEAD_REDIS)

    allowed, retry_after = asyncio.run(backend.check_and_record("ip:1.2.3.4", 5, 60))

    assert (allowed, retry_after) == (True, 0)


def test_requests_are_still_limited_while_redis_is_down():
    backend = RedisBackend(DEAD_REDIS)

    async def burst():
        return [await backend.check_and_record("ip:1.2.3.4", 3, 60) for _ in range(5)]

    results = asyncio.run(burst())

    assert [ok for ok, _ in results] == [True, True, True, False, False]
    assert results[3][1] >= 1


def test_an_unreachable_redis_fails_fast_not_after_a_long_hang():
    backend = RedisBackend("redis://10.255.255.1:6379/0")  # unroutable: connect would hang
    started = time.perf_counter()

    asyncio.run(backend.check_and_record("ip:9.9.9.9", 5, 60))

    assert time.perf_counter() - started < 5


def test_the_redis_password_never_reaches_the_log(caplog, monkeypatch):
    from shared.config import settings

    monkeypatch.setattr(settings, "redis_url", DEAD_REDIS)
    with caplog.at_level(logging.DEBUG):
        backend = rate_limit.get_rate_limit_backend()
        asyncio.run(backend.check_and_record("ip:1.2.3.4", 5, 60))

    assert "S3cretPw" not in caplog.text
    assert _redact_url(DEAD_REDIS) == "redis://127.0.0.1:1"
    assert _redact_url("rediss://u:p@redis.internal/0") == "rediss://redis.internal"


def test_in_memory_keys_for_departed_clients_are_swept(monkeypatch):
    monkeypatch.setattr(rate_limit, "_SWEEP_EVERY", 10)
    backend = InMemoryBackend()
    old = time.time() - rate_limit._STALE_AFTER_SECONDS - 5
    for i in range(500):
        backend._hits[f"ip:10.0.{i // 256}.{i % 256}"] = [old]

    async def traffic():
        for _ in range(10):
            await backend.check_and_record("ip:active", 100, 60)

    asyncio.run(traffic())

    assert list(backend._hits) == ["ip:active"]
