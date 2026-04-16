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

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

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
        return True, 0


class RedisBackend(RateLimitBackend):
    """Sliding-window counter backed by Redis sorted sets.

    Each key is a sorted set where the score is the request timestamp.
    Atomic pipeline: ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE.
    """

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def check_and_record(
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
            logger.info("Rate limiter: using Redis backend (%s)", settings.redis_url)
            return backend
        except Exception as exc:
            logger.warning(
                "Rate limiter: Redis unavailable (%s), falling back to in-memory. Error: %s",
                settings.redis_url, exc,
            )

    logger.info("Rate limiter: using in-memory backend")
    return InMemoryBackend()
