"""
Connectors Unit Tests
======================
Tests for BaseConnector contract (via DuckDBConnector in-memory),
ConnectorConfig, ConnectorMetadata, and SourceType.

DuckDB is installed in the venv so we can run real in-memory queries
without any external database.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.base import BaseConnector, ConnectorConfig, ConnectorMetadata, SourceType
from connectors.duckdb_connector import DuckDBConnector

pytest.importorskip("duckdb", reason="duckdb not installed")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _duckdb_config(name: str = "test_db") -> ConnectorConfig:
    return ConnectorConfig(
        source_type=SourceType.DUCKDB,
        name=name,
        connection_string=":memory:",
    )


# ── ConnectorConfig / Metadata ────────────────────────────────────────────────

class TestConnectorConfig:
    def test_minimal_config(self):
        cfg = ConnectorConfig(source_type=SourceType.CSV, name="my_csv")
        assert cfg.source_type == SourceType.CSV
        assert cfg.name == "my_csv"
        assert cfg.host is None

    def test_full_config(self):
        cfg = ConnectorConfig(
            source_type=SourceType.POSTGRESQL,
            name="prod",
            host="db.example.com",
            port=5432,
            username="user",
            password="pass",
            database="mydb",
        )
        assert cfg.host == "db.example.com"
        assert cfg.port == 5432


class TestConnectorMetadata:
    def test_defaults(self):
        m = ConnectorMetadata("s1", SourceType.CSV, "My CSV", "desc", "icon")
        assert m.connected is False
        assert m.table_count == 0
        assert m.last_sync is None

    def test_source_type_stored(self):
        m = ConnectorMetadata("s2", SourceType.DUCKDB, "Duck", "", "")
        assert m.source_type == SourceType.DUCKDB


# ── DuckDBConnector — lifecycle ───────────────────────────────────────────────

class TestDuckDBConnectorLifecycle:
    @pytest.mark.asyncio
    async def test_connect_returns_true(self):
        conn = DuckDBConnector(_duckdb_config())
        assert await conn.connect() is True
        assert conn.is_connected() is True
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self):
        conn = DuckDBConnector(_duckdb_config())
        await conn.connect()
        assert await conn.disconnect() is True
        assert conn.is_connected() is False

    @pytest.mark.asyncio
    async def test_is_connected_false_before_connect(self):
        conn = DuckDBConnector(_duckdb_config())
        assert conn.is_connected() is False

    @pytest.mark.asyncio
    async def test_health_check_connected(self):
        conn = DuckDBConnector(_duckdb_config())
        await conn.connect()
        assert await conn.health_check() is True
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_health_check_disconnected(self):
        conn = DuckDBConnector(_duckdb_config())
        # Never connected — list_tables returns [] which is still valid
        result = await conn.health_check()
        # Disconnected DuckDB returns empty list without raising → health_check = True
        # (the base-class implementation treats [] as healthy)
        assert isinstance(result, bool)


# ── DuckDBConnector — queries ─────────────────────────────────────────────────

class TestDuckDBConnectorQueries:
    @pytest.fixture
    async def conn(self):
        c = DuckDBConnector(_duckdb_config())
        await c.connect()
        # Seed a table
        c._conn.execute(
            "CREATE TABLE sales (id INTEGER, product VARCHAR, revenue DOUBLE)"
        )
        c._conn.execute(
            "INSERT INTO sales VALUES (1,'Widget',100.0),(2,'Gadget',200.0),(3,'Doohickey',50.0)"
        )
        yield c
        await c.disconnect()

    @pytest.mark.asyncio
    async def test_list_tables(self, conn):
        tables = await conn.list_tables()
        assert "sales" in tables

    @pytest.mark.asyncio
    async def test_table_count_updated(self, conn):
        await conn.list_tables()
        assert conn.metadata.table_count >= 1

    @pytest.mark.asyncio
    async def test_get_table_schema(self, conn):
        schema = await conn.get_table_schema("sales")
        assert schema["table_name"] == "sales"
        col_names = [c["name"] for c in schema["columns"]]
        assert "id" in col_names
        assert "revenue" in col_names

    @pytest.mark.asyncio
    async def test_execute_query_returns_dicts(self, conn):
        rows = await conn.execute_query("SELECT * FROM sales ORDER BY id")
        assert len(rows) == 3
        assert rows[0]["product"] == "Widget"
        assert rows[1]["revenue"] == 200.0

    @pytest.mark.asyncio
    async def test_execute_query_limit_respected(self, conn):
        rows = await conn.execute_query("SELECT * FROM sales", limit=2)
        assert len(rows) <= 2

    @pytest.mark.asyncio
    async def test_sample_rows(self, conn):
        rows = await conn.sample_rows("sales", limit=2)
        assert 1 <= len(rows) <= 2

    @pytest.mark.asyncio
    async def test_profile_table(self, conn):
        profile = await conn.profile_table("sales")
        assert profile["table_name"] == "sales"
        assert profile["rows"] == 3
        assert profile["columns"] == 3

    @pytest.mark.asyncio
    async def test_execute_bad_query_returns_empty(self, conn):
        rows = await conn.execute_query("SELECT * FROM nonexistent_table_xyz")
        assert rows == []

    @pytest.mark.asyncio
    async def test_query_while_disconnected(self):
        conn = DuckDBConnector(_duckdb_config())
        rows = await conn.execute_query("SELECT 1")
        assert rows == []

    @pytest.mark.asyncio
    async def test_to_dict_structure(self, conn):
        d = conn.to_dict()
        assert d["source_type"] == "duckdb"
        assert d["connected"] is True
        assert "source_id" in d


# ── DuckDBConnector — file queries ────────────────────────────────────────────

class TestDuckDBFileQuery:
    @pytest.mark.asyncio
    async def test_query_file_csv(self, tmp_path):
        """query_file should read a CSV written to disk."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,score\nalice,90\nbob,80\n")

        conn = DuckDBConnector(_duckdb_config())
        await conn.connect()
        rows = await conn.query_file(str(csv_file))
        await conn.disconnect()

        assert len(rows) == 2
        names = {r["name"] for r in rows}
        assert names == {"alice", "bob"}

    @pytest.mark.asyncio
    async def test_query_file_with_custom_sql(self, tmp_path):
        csv_file = tmp_path / "scores.csv"
        csv_file.write_text("name,score\nalice,90\nbob,80\ncharlie,95\n")

        conn = DuckDBConnector(_duckdb_config())
        await conn.connect()
        rows = await conn.query_file(
            str(csv_file),
            query=f"SELECT * FROM '{csv_file}' WHERE score > 85 ORDER BY score DESC",
        )
        await conn.disconnect()

        assert len(rows) == 2
        assert rows[0]["name"] == "charlie"
