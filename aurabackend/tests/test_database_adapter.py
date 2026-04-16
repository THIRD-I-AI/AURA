"""
AURA Database Adapter Tests
=============================
Tests for the universal database adapter: config, factory, DuckDB adapter,
PostgresAdapter (mocked), and abstract base-class behaviour.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database_adapter import (
    AdapterConfig,
    BackendType,
    DatabaseAdapter,
    DuckDBAdapter,
    PostgresAdapter,
    _adapter_cache,
    _config_from_env,
    available_adapters,
    get_adapter,
)


# ── Helpers ────────────────────────────────────────────────────────

def _clear_cache():
    """Remove cached adapters so each test is isolated."""
    _adapter_cache.clear()


# ── BackendType / AdapterConfig ────────────────────────────────────

class TestBackendType:
    def test_enum_values(self):
        assert BackendType.POSTGRESQL == "postgresql"
        assert BackendType.DUCKDB == "duckdb"
        assert BackendType.MYSQL == "mysql"
        assert BackendType.BIGQUERY == "bigquery"
        assert BackendType.SNOWFLAKE == "snowflake"

    def test_from_string(self):
        assert BackendType("postgresql") is BackendType.POSTGRESQL
        assert BackendType("duckdb") is BackendType.DUCKDB


class TestAdapterConfig:
    def test_defaults(self):
        cfg = AdapterConfig()
        assert cfg.backend == BackendType.POSTGRESQL
        assert cfg.host == "localhost"
        assert cfg.port == 5432
        assert cfg.database == "aura_vault"
        assert cfg.username == "postgres"
        assert cfg.password == ""
        assert cfg.db_path == ""
        assert cfg.extra == {}

    def test_custom_values(self):
        cfg = AdapterConfig(
            backend=BackendType.DUCKDB,
            host="remotehost",
            port=9999,
            database="testdb",
            username="admin",
            password="secret",
            db_path="/tmp/test.duckdb",
            extra={"timeout": 30},
        )
        assert cfg.backend == BackendType.DUCKDB
        assert cfg.host == "remotehost"
        assert cfg.port == 9999
        assert cfg.extra == {"timeout": 30}


# ── Config from env ───────────────────────────────────────────────

class TestConfigFromEnv:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("AURA_VAULT_BACKEND", raising=False)
        monkeypatch.delenv("AURA_VAULT_HOST", raising=False)
        cfg = _config_from_env()
        assert cfg.backend == BackendType.POSTGRESQL
        assert cfg.host == "localhost"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AURA_VAULT_BACKEND", "duckdb")
        monkeypatch.setenv("AURA_VAULT_HOST", "myhost")
        monkeypatch.setenv("AURA_VAULT_PORT", "3306")
        monkeypatch.setenv("AURA_VAULT_DATABASE", "mydb")
        monkeypatch.setenv("AURA_VAULT_USER", "myuser")
        monkeypatch.setenv("AURA_VAULT_PASSWORD", "mypass")
        monkeypatch.setenv("AURA_VAULT_DUCKDB_PATH", "/tmp/duck.db")
        cfg = _config_from_env()
        assert cfg.backend == BackendType.DUCKDB
        assert cfg.host == "myhost"
        assert cfg.port == 3306
        assert cfg.database == "mydb"
        assert cfg.username == "myuser"
        assert cfg.password == "mypass"
        assert cfg.db_path == "/tmp/duck.db"


# ── Abstract base class ──────────────────────────────────────────

class TestDatabaseAdapterBase:
    def test_is_connected_default_false(self):
        cfg = AdapterConfig()
        # Can't instantiate ABC directly; use DuckDBAdapter as concrete
        adapter = DuckDBAdapter(cfg)
        assert adapter.is_connected is False

    @pytest.mark.asyncio
    async def test_vector_search_raises(self):
        cfg = AdapterConfig()
        adapter = DuckDBAdapter(cfg)
        with pytest.raises(NotImplementedError, match="does not support vector search"):
            await adapter.vector_search("t", [1.0, 2.0])

    @pytest.mark.asyncio
    async def test_store_vector_raises(self):
        cfg = AdapterConfig()
        adapter = DuckDBAdapter(cfg)
        with pytest.raises(NotImplementedError, match="does not support vector storage"):
            await adapter.store_vector("t", {"a": 1}, [1.0])

    @pytest.mark.asyncio
    async def test_store_point_raises(self):
        cfg = AdapterConfig()
        adapter = DuckDBAdapter(cfg)
        with pytest.raises(NotImplementedError, match="does not support spatial storage"):
            await adapter.store_point("t", {"a": 1}, 1.0, 2.0)

    @pytest.mark.asyncio
    async def test_capabilities_default(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        caps = await adapter.capabilities()
        assert caps["relational"] is True
        assert caps["vector"] is False
        assert caps["spatial"] is False


# ── DuckDB Adapter ───────────────────────────────────────────────

class TestDuckDBAdapter:
    @pytest.mark.asyncio
    async def test_connect_memory(self):
        _clear_cache()
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        result = await adapter.connect()
        assert result is True
        assert adapter.is_connected is True
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        result = await adapter.disconnect()
        assert result is True
        assert adapter.is_connected is False

    @pytest.mark.asyncio
    async def test_health_check_connected(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        health = await adapter.health_check()
        assert health["ok"] is True
        assert health["backend"] == "duckdb"
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_health_check_not_connected(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        health = await adapter.health_check()
        assert health["ok"] is False

    @pytest.mark.asyncio
    async def test_execute_query(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        rows = await adapter.execute_query("SELECT 42 AS answer")
        assert len(rows) == 1
        assert rows[0]["answer"] == 42
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_execute_query_not_connected(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        rows = await adapter.execute_query("SELECT 1")
        assert rows == []

    @pytest.mark.asyncio
    async def test_execute_query_with_params(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        rows = await adapter.execute_query("SELECT ? + 1 AS val", [10])
        assert rows[0]["val"] == 11
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_execute_write(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        await adapter.execute_write("CREATE TABLE test_tbl (id INTEGER, name VARCHAR)")
        affected = await adapter.execute_write("INSERT INTO test_tbl VALUES (1, 'Alice')")
        assert affected >= 0  # DuckDB changes() may vary
        rows = await adapter.execute_query("SELECT * FROM test_tbl")
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_execute_write_not_connected(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        result = await adapter.execute_write("INSERT INTO x VALUES (1)")
        assert result == 0

    @pytest.mark.asyncio
    async def test_list_tables(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        await adapter.execute_write("CREATE TABLE alpha (id INTEGER)")
        await adapter.execute_write("CREATE TABLE beta (id INTEGER)")
        tables = await adapter.list_tables()
        assert "alpha" in tables
        assert "beta" in tables
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_table_schema(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        await adapter.execute_write("CREATE TABLE schema_test (id INTEGER, name VARCHAR)")
        schema = await adapter.get_table_schema("schema_test")
        assert schema["table"] == "schema_test"
        assert len(schema["columns"]) == 2
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_spatial_query_delegates_to_execute_query(self):
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = DuckDBAdapter(cfg)
        await adapter.connect()
        rows = await adapter.spatial_query("SELECT 99 AS geo_val")
        assert rows[0]["geo_val"] == 99
        await adapter.disconnect()


# ── PostgresAdapter (mocked) ─────────────────────────────────────

class TestPostgresAdapter:
    def test_init(self):
        cfg = AdapterConfig(backend=BackendType.POSTGRESQL)
        adapter = PostgresAdapter(cfg)
        assert adapter.is_connected is False
        assert adapter._has_pgvector is False
        assert adapter._has_postgis is False

    @pytest.mark.asyncio
    async def test_health_check_not_connected(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        health = await adapter.health_check()
        assert health["ok"] is False
        assert "not connected" in health["error"]

    @pytest.mark.asyncio
    async def test_execute_query_no_pool(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        rows = await adapter.execute_query("SELECT 1")
        assert rows == []

    @pytest.mark.asyncio
    async def test_execute_write_no_pool(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        result = await adapter.execute_write("INSERT INTO x VALUES (1)")
        assert result == 0

    @pytest.mark.asyncio
    async def test_disconnect(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        result = await adapter.disconnect()
        assert result is True
        assert adapter.is_connected is False

    @pytest.mark.asyncio
    async def test_vector_search_no_pgvector(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        adapter._has_pgvector = False
        with pytest.raises(NotImplementedError, match="pgvector"):
            await adapter.vector_search("t", [1.0, 2.0])

    @pytest.mark.asyncio
    async def test_store_vector_no_pgvector(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        adapter._has_pgvector = False
        with pytest.raises(NotImplementedError, match="pgvector"):
            await adapter.store_vector("t", {"col": "val"}, [1.0])

    @pytest.mark.asyncio
    async def test_store_point_no_postgis(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        adapter._has_postgis = False
        with pytest.raises(NotImplementedError, match="PostGIS"):
            await adapter.store_point("t", {"col": "val"}, 1.0, 2.0)

    @pytest.mark.asyncio
    async def test_capabilities(self):
        cfg = AdapterConfig()
        adapter = PostgresAdapter(cfg)
        adapter._has_pgvector = True
        adapter._has_postgis = True
        caps = await adapter.capabilities()
        assert caps == {"relational": True, "vector": True, "spatial": True}


# ── Factory ──────────────────────────────────────────────────────

class TestGetAdapter:
    def test_returns_duckdb_adapter(self):
        _clear_cache()
        adapter = get_adapter("duckdb", cache_key="test_duckdb")
        assert isinstance(adapter, DuckDBAdapter)
        _clear_cache()

    def test_returns_postgres_adapter(self):
        _clear_cache()
        adapter = get_adapter("postgresql", cache_key="test_pg")
        assert isinstance(adapter, PostgresAdapter)
        _clear_cache()

    def test_default_adapter_from_env(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("AURA_VAULT_BACKEND", "duckdb")
        adapter = get_adapter(cache_key="test_env")
        assert isinstance(adapter, DuckDBAdapter)
        _clear_cache()

    def test_caching(self):
        _clear_cache()
        a1 = get_adapter("duckdb", cache_key="cache_test")
        a2 = get_adapter("duckdb", cache_key="cache_test")
        assert a1 is a2
        _clear_cache()

    def test_explicit_config(self):
        _clear_cache()
        cfg = AdapterConfig(backend=BackendType.DUCKDB, db_path=":memory:")
        adapter = get_adapter(config=cfg, cache_key="explicit_cfg")
        assert isinstance(adapter, DuckDBAdapter)
        assert adapter.config.db_path == ":memory:"
        _clear_cache()

    def test_overrides(self):
        _clear_cache()
        adapter = get_adapter("postgresql", cache_key="override_test", host="custom-host", port=1234)
        assert adapter.config.host == "custom-host"
        assert adapter.config.port == 1234
        _clear_cache()


class TestAvailableAdapters:
    def test_returns_list(self):
        result = available_adapters()
        assert isinstance(result, list)
        assert len(result) == 2
        names = [r["backend"] for r in result]
        assert "postgresql" in names
        assert "duckdb" in names
        for entry in result:
            assert "available" in entry
            assert isinstance(entry["available"], bool)
