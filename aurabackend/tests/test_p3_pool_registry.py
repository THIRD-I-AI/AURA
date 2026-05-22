"""
Sprint P-3 -- PostgreSQL connection pool registry tests (finding #6).

Verifies that _get_or_create_pg_pool reuses pools across calls and that
close_all_pg_pools drains the registry cleanly.  asyncpg is injected via
sys.modules so the test suite runs without a real Postgres install.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fresh_registry():
    """Isolate each test by resetting the module-level pool registry."""
    import api_gateway.routers.queries as q

    snapshot = dict(q._pg_pool_registry)
    q._pg_pool_registry.clear()
    yield
    q._pg_pool_registry.clear()
    q._pg_pool_registry.update(snapshot)


@pytest.fixture()
def mock_asyncpg(monkeypatch):
    """Inject a fake asyncpg module so _get_or_create_pg_pool can run."""
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "asyncpg", fake)
    return fake


def _cfg(host: str = "localhost", db: str = "testdb"):
    from connectors.base import ConnectorConfig, SourceType

    return ConnectorConfig(
        source_type=SourceType.POSTGRESQL,
        name="test",
        host=host,
        port=5432,
        database=db,
        username="user",
        password="secret",
    )


class TestPgPoolKey:
    def test_same_config_same_key(self):
        from api_gateway.routers.queries import _pg_pool_key

        assert _pg_pool_key(_cfg()) == _pg_pool_key(_cfg())

    def test_different_host_different_key(self):
        from api_gateway.routers.queries import _pg_pool_key

        assert _pg_pool_key(_cfg("host1")) != _pg_pool_key(_cfg("host2"))

    def test_different_db_different_key(self):
        from api_gateway.routers.queries import _pg_pool_key

        assert _pg_pool_key(_cfg(db="db1")) != _pg_pool_key(_cfg(db="db2"))


class TestGetOrCreatePgPool:
    @pytest.mark.asyncio
    async def test_pool_created_once_for_same_config(self, mock_asyncpg):
        import api_gateway.routers.queries as q

        mock_pool = MagicMock()
        mock_pool.close = AsyncMock()
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

        cfg = _cfg()
        p1 = await q._get_or_create_pg_pool(cfg)
        p2 = await q._get_or_create_pg_pool(cfg)

        assert p1 is p2
        assert mock_asyncpg.create_pool.call_count == 1

    @pytest.mark.asyncio
    async def test_different_configs_get_separate_pools(self, mock_asyncpg):
        import api_gateway.routers.queries as q

        pool_a, pool_b = MagicMock(), MagicMock()
        pool_a.close = pool_b.close = AsyncMock()
        mock_asyncpg.create_pool = AsyncMock(side_effect=[pool_a, pool_b])

        pa = await q._get_or_create_pg_pool(_cfg(db="db_a"))
        pb = await q._get_or_create_pg_pool(_cfg(db="db_b"))

        assert pa is not pb
        assert mock_asyncpg.create_pool.call_count == 2


class TestCloseAllPgPools:
    @pytest.mark.asyncio
    async def test_close_called_on_all_pools(self, mock_asyncpg):
        import api_gateway.routers.queries as q

        pool = MagicMock()
        pool.close = AsyncMock()
        mock_asyncpg.create_pool = AsyncMock(return_value=pool)

        await q._get_or_create_pg_pool(_cfg())
        await q.close_all_pg_pools()

        pool.close.assert_awaited_once()
        assert len(q._pg_pool_registry) == 0

    @pytest.mark.asyncio
    async def test_close_all_empties_registry(self, mock_asyncpg):
        import api_gateway.routers.queries as q

        pools = []
        for _ in range(3):
            p = MagicMock()
            p.close = AsyncMock()
            pools.append(p)
        mock_asyncpg.create_pool = AsyncMock(side_effect=pools)

        for db in ("d1", "d2", "d3"):
            await q._get_or_create_pg_pool(_cfg(db=db))

        assert len(q._pg_pool_registry) == 3
        await q.close_all_pg_pools()
        assert len(q._pg_pool_registry) == 0
        for p in pools:
            p.close.assert_awaited_once()
