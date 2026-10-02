"""
AURA Rate-Limit Backends
==========================
Pluggable sliding-window rate-limit storage.

- **InMemoryBackend**: default, zero-dependency, single-process only.
- **RedisBackend**: production-grade, cross-process, requires Redis.

Usage:
    from shared.rate_limit import get_rate_limit_backend

    backend = get_rate_limit_backend()           # auto-detect
    allowed, retry_after = await backend.check_and_record("1.2.3.4", 100, 60)
"""
from __future__ import annotations

import logging
import time
import weakref
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Tuple

logger = logging.getLogger(__name__)


class RateLimitBackend(ABC):
    """Abstract sliding-window rate limiter."""

    @abstractmethod
    async def check_and_record(
        self, key: str, max_requests: int, window_seconds: int,
    ) -> Tuple[bool, int]:
        """Check if *key* is within the rate limit and record the request.

        Returns
        -------
        (allowed, retry_after)
            ``allowed`` is True if the request should proceed.
            ``retry_after`` is the number of seconds until the next slot
            opens (only meaningful when ``allowed`` is False).
        """
        ...


class InMemoryBackend(RateLimitBackend):
    """In-process sliding-window counter using plain Python lists.

    Good for development and single-process deployments.
    Not shared across workers or restarts.
    """

    # Every live instance, weakly held. The sliding window is per-IP but
    # process-global, and under TestClient the whole suite shares one client
    # host — so without a per-test reset the 11th auth request ANYWHERE in a
    # run 429s a test that never touched throttling. See the autouse fixture
    # in tests/conftest.py. WeakSet so this never pins a backend alive.
    _instances: "weakref.WeakSet" = weakref.WeakSet()

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._calls = 0
        InMemoryBackend._instances.add(self)

    @classmethod
    def reset_all(cls) -> None:
        """Clear counters on every live in-memory backend (test isolation)."""
        for inst in list(cls._instances):
            inst._hits.clear()

    async def check_and_record(
        self, key: str, max_requests: int, window_seconds: int,
    ) -> Tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        self._hits[key] = [t for t in self._hits[key] if t > cutoff]

        if len(self._hits[key]) >= max_requests:
            retry_after = int(window_seconds - (now - self._hits[key][0])) + 1
            return False, retry_after

        self._hits[key].append(now)
        self._calls += 1
        if self._calls % _SWEEP_EVERY == 0:
            self._sweep(now)
        return True, 0

    def _sweep(self, now: float) -> None:
        """Drop keys with no recent hit.

        BUG-317: a key was only ever pruned when the same client came back, so every
        address ever seen kept a dict entry for the life of the process.
        """
        stale = [k for k, hits in self._hits.items() if not hits or hits[-1] < now - _STALE_AFTER_SECONDS]
        for k in stale:
            del self._hits[k]


# Longer than any window this limiter is configured with.
_STALE_AFTER_SECONDS = 3600
_SWEEP_EVERY = 1000
_REDIS_TIMEOUT_SECONDS = 1.0


def _redact_url(url: str) -> str:
    """A connection URL without its credentials, for logs (BUG-307)."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
        return f"{parts.scheme}://{host}:{parts.port}" if parts.port else f"{parts.scheme}://{host}"
    except ValueError:
        return "<unparseable url>"


class RedisBackend(RateLimitBackend):
    """Sliding-window counter backed by Redis sorted sets.

    Each key is a sorted set where the score is the request timestamp.
    Atomic pipeline: ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE.
    """

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis
        # BUG-313: from_url is lazy and had no timeouts, so an unreachable Redis was
        # only discovered per request -- as an unhandled error (500 on every route) or
        # a hang with no deadline.
        self._redis = aioredis.from_url(
            redis_url, decode_responses=True,
            socket_timeout=_REDIS_TIMEOUT_SECONDS, socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
        )
        self._local = InMemoryBackend()
        self._last_warned = 0.0

    async def check_and_record(
        self, key: str, max_requests: int, window_seconds: int,
    ) -> Tuple[bool, int]:
        """Check against Redis; while Redis is unavailable, limit per process instead.

        A rate limiter that is down must not take the API down with it, and must not
        stop limiting either.
        """
        try:
            return await self._check_redis(key, max_requests, window_seconds)
        except Exception as exc:
            now = time.time()
            if now - self._last_warned > 60:
                self._last_warned = now
                logger.warning(
                    "Rate limiter: Redis unavailable (%s); limiting in-process until it returns",
                    type(exc).__name__,
                )
            return await self._local.check_and_record(key, max_requests, window_seconds)

    async def _check_redis(
        self, key: str, max_requests: int, window_seconds: int,
    ) -> Tuple[bool, int]:
        now = time.time()
        redis_key = f"aura:rl:{key}"
        cutoff = now - window_seconds

        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(redis_key, "-inf", cutoff)
        pipe.zcard(redis_key)
        pipe.zadd(redis_key, {str(now): now})
        pipe.expire(redis_key, window_seconds + 1)
        results = await pipe.execute()

        count = results[1]  # ZCARD result (after prune, before add)
        if count >= max_requests:
            # Find the oldest timestamp to compute retry_after
            oldest = await self._redis.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(window_seconds - (now - oldest[0][1])) + 1
            else:
                retry_after = 1
            # Remove the ZADD we just did since the request is rejected
            await self._redis.zrem(redis_key, str(now))
            return False, max(retry_after, 1)

        return True, 0

    async def close(self) -> None:
        await self._redis.close()


def get_rate_limit_backend() -> RateLimitBackend:
    """Build the best available backend based on configuration.

    Returns ``RedisBackend`` if ``AURA_REDIS_URL`` is set and Redis is
    reachable; otherwise falls back to ``InMemoryBackend``.
    """
    from shared.config import settings

    if settings.redis_url:
        try:
            backend = RedisBackend(settings.redis_url)
            logger.info("Rate limiter: using Redis backend (%s)", _redact_url(settings.redis_url))
            return backend
        except Exception as exc:
            logger.warning(
                "Rate limiter: Redis backend could not be created (%s), using in-memory. Error: %s",
                _redact_url(settings.redis_url), type(exc).__name__,
            )

    logger.info("Rate limiter: using in-memory backend")
    return InMemoryBackend()
