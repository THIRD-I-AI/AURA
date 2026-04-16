"""
AURA Rate-Limit Backend Tests
===============================
Tests for InMemoryBackend and the middleware integration.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.rate_limit import InMemoryBackend, RedisBackend


# ── RedisBackend (mocked) ─────────────────────────────────────────────

class TestRedisBackend:
    @pytest.fixture()
    def mock_redis(self):
        from unittest.mock import AsyncMock, MagicMock
        redis_mock = MagicMock()
        # Async methods on the redis client
        redis_mock.zrange = AsyncMock()
        redis_mock.zrem = AsyncMock()
        redis_mock.close = AsyncMock()
        # pipeline() is sync, returns a Pipeline with sync queue methods + async execute
        pipe_mock = MagicMock()
        pipe_mock.execute = AsyncMock()
        redis_mock.pipeline.return_value = pipe_mock
        return redis_mock, pipe_mock

    async def test_allows_under_limit(self, mock_redis):
        redis_mock, pipe_mock = mock_redis
        # ZREMRANGEBYSCORE, ZCARD=2, ZADD, EXPIRE
        pipe_mock.execute.return_value = [0, 2, 1, True]

        backend = RedisBackend.__new__(RedisBackend)
        backend._redis = redis_mock

        allowed, retry = await backend.check_and_record("1.2.3.4", max_requests=5, window_seconds=60)
        assert allowed is True
        assert retry == 0

    async def test_blocks_over_limit(self, mock_redis):
        redis_mock, pipe_mock = mock_redis
        # ZCARD returns 5 (at limit)
        pipe_mock.execute.return_value = [0, 5, 1, True]
        redis_mock.zrange.return_value = [("1234567890", 1000.0)]

        backend = RedisBackend.__new__(RedisBackend)
        backend._redis = redis_mock

        allowed, retry = await backend.check_and_record("1.2.3.4", max_requests=5, window_seconds=60)
        assert allowed is False
        assert retry >= 1
        redis_mock.zrem.assert_called_once()

    async def test_blocks_with_empty_oldest(self, mock_redis):
        redis_mock, pipe_mock = mock_redis
        pipe_mock.execute.return_value = [0, 10, 1, True]
        redis_mock.zrange.return_value = []  # no oldest entry

        backend = RedisBackend.__new__(RedisBackend)
        backend._redis = redis_mock

        allowed, retry = await backend.check_and_record("1.2.3.4", max_requests=5, window_seconds=60)
        assert allowed is False
        assert retry == 1

    async def test_close(self, mock_redis):
        redis_mock, _ = mock_redis
        backend = RedisBackend.__new__(RedisBackend)
        backend._redis = redis_mock
        await backend.close()
        redis_mock.close.assert_called_once()


# ── Factory with Redis URL ────────────────────────────────────────────

class TestGetRateLimitBackendRedis:
    def test_returns_redis_backend_when_url_set(self, monkeypatch):
        from unittest.mock import patch
        from shared.config import settings
        monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379")

        with patch("shared.rate_limit.RedisBackend") as mock_cls:
            mock_cls.return_value = mock_cls
            from shared.rate_limit import get_rate_limit_backend
            backend = get_rate_limit_backend()
            mock_cls.assert_called_once_with("redis://localhost:6379")

    def test_falls_back_on_redis_error(self, monkeypatch):
        from unittest.mock import patch
        from shared.config import settings
        monkeypatch.setattr(settings, "redis_url", "redis://bad-host:6379")

        with patch("shared.rate_limit.RedisBackend", side_effect=Exception("conn refused")):
            from shared.rate_limit import get_rate_limit_backend
            backend = get_rate_limit_backend()
            assert isinstance(backend, InMemoryBackend)

class TestInMemoryBackend:
    @pytest.fixture()
    def backend(self):
        return InMemoryBackend()

    async def test_allows_under_limit(self, backend):
        allowed, _ = await backend.check_and_record("1.2.3.4", max_requests=5, window_seconds=60)
        assert allowed is True

    async def test_allows_up_to_limit(self, backend):
        for _ in range(5):
            allowed, _ = await backend.check_and_record("1.2.3.4", max_requests=5, window_seconds=60)
            assert allowed is True

    async def test_blocks_over_limit(self, backend):
        for _ in range(5):
            await backend.check_and_record("1.2.3.4", max_requests=5, window_seconds=60)

        allowed, retry_after = await backend.check_and_record("1.2.3.4", max_requests=5, window_seconds=60)
        assert allowed is False
        assert retry_after > 0

    async def test_different_keys_independent(self, backend):
        for _ in range(5):
            await backend.check_and_record("1.1.1.1", max_requests=5, window_seconds=60)

        # Different IP should still be allowed
        allowed, _ = await backend.check_and_record("2.2.2.2", max_requests=5, window_seconds=60)
        assert allowed is True

    async def test_window_expiry(self, backend):
        # Fill up the limit with a tiny window
        for _ in range(3):
            await backend.check_and_record("1.2.3.4", max_requests=3, window_seconds=1)

        # Should be blocked
        allowed, _ = await backend.check_and_record("1.2.3.4", max_requests=3, window_seconds=1)
        assert allowed is False

        # Wait for window to expire
        await asyncio.sleep(1.1)

        # Should be allowed again
        allowed, _ = await backend.check_and_record("1.2.3.4", max_requests=3, window_seconds=1)
        assert allowed is True


# ── Middleware integration ─────────────────────────────────────────────

class TestRateLimitMiddleware:
    @pytest.fixture()
    def client(self):
        """Create a minimal FastAPI app with rate limiting."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from shared.middleware import RateLimitMiddleware
        from shared.rate_limit import InMemoryBackend

        app = FastAPI()
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_window=3,
            window_seconds=60,
            backend=InMemoryBackend(),
        )

        @app.get("/test")
        def test_endpoint():
            return {"ok": True}

        @app.get("/health")
        def health():
            return {"status": "healthy"}

        return TestClient(app)

    def test_allows_requests_under_limit(self, client):
        for _ in range(3):
            resp = client.get("/test")
            assert resp.status_code == 200

    def test_blocks_requests_over_limit(self, client):
        for _ in range(3):
            client.get("/test")

        resp = client.get("/test")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert resp.json()["error"] == "RATE_LIMITED"

    def test_health_exempt_from_rate_limit(self, client):
        # Exhaust the limit on /test
        for _ in range(3):
            client.get("/test")

        # /health should still work
        resp = client.get("/health")
        assert resp.status_code == 200


# ── Factory auto-detect ────────────────────────────────────────────────

class TestGetRateLimitBackend:
    def test_returns_in_memory_when_no_redis(self, monkeypatch):
        monkeypatch.delenv("AURA_REDIS_URL", raising=False)
        from shared import config as config_mod
        config_mod.get_settings.cache_clear()

        from shared.rate_limit import InMemoryBackend, get_rate_limit_backend
        backend = get_rate_limit_backend()
        assert isinstance(backend, InMemoryBackend)
