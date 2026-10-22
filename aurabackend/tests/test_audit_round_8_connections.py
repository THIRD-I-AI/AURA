"""Audit round 8 (connections): BUG-505.

The ingest route is called directly with real connector classes (DuckDB on a local
file; MySQL and BigQuery with their network client replaced) so no FastAPI lifespan
runs and no database or cloud service is contacted.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import connections  # noqa: E402
from connectors.base import ConnectorConfig, SourceType  # noqa: E402


def _request(tenant: str = "tenant-a") -> Request:
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
    req.state.user = {"org_id": tenant, "sub": "user-1"}
    return req


@pytest.fixture()
def uasr(monkeypatch):
    """Record the rows sent to /uasr/ingest; no metadata store."""
    sent = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"drift_detected": False}

    class _UASR:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            if url.endswith("/uasr/ingest"):
                sent.extend(json["rows"])
            return _Resp()

    monkeypatch.setattr(connections.httpx, "AsyncClient", _UASR)
    import metadata_store.repository as repo_mod

    async def _no_repo():
        if False:
            yield None

    monkeypatch.setattr(repo_mod, "get_repository", _no_repo)
    return sent


def _use_connector(monkeypatch, connector):
    async def _make(conn_type, config, request):
        return connector

    monkeypatch.setattr(connections, "_make_connector", _make)


def _ingest(connector_type, table="orders", batch_size=4):
    req = connections.ConnectorIngestRequest(table_name=table, batch_size=batch_size, register_baseline=False)
    return asyncio.run(connections.ingest_connector_data(connector_type, req, _request()))


# ── BUG-505 ──────────────────────────────────────────────────────────


def test_bug505_duckdb_failed_page_query_is_not_reported_as_empty_success(tmp_path, monkeypatch, uasr):
    import duckdb

    from connectors.duckdb_connector import DuckDBConnector

    db = tmp_path / "src.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE customers AS SELECT range AS id FROM range(10)")
    con.close()
    connector = DuckDBConnector(ConnectorConfig(
        source_type=SourceType.DUCKDB, name="t", connection_string=str(db),
    ))
    _use_connector(monkeypatch, connector)
    # 'orders' is not in this database, so every page query fails -- the same shape
    # as BigQuery rejecting an unqualified table name. It used to come back as
    # success:true, total_rows:0.
    with pytest.raises(HTTPException) as exc:
        _ingest("duckdb", table="orders")
    assert exc.value.status_code == 502
    assert uasr == []


def test_bug505_duckdb_real_table_still_ingests(tmp_path, monkeypatch, uasr):
    import duckdb

    from connectors.duckdb_connector import DuckDBConnector

    db = tmp_path / "src.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE orders AS SELECT range AS id FROM range(10)")
    con.close()
    connector = DuckDBConnector(ConnectorConfig(
        source_type=SourceType.DUCKDB, name="t", connection_string=str(db),
    ))
    _use_connector(monkeypatch, connector)
    out = _ingest("duckdb", table="orders")
    assert out["success"] is True
    assert out["total_rows"] == 10
    assert len(uasr) == 10


class _FlakyMySQLPool:
    """aiomysql-shaped pool over a 10-row table whose third page query fails
    (lock wait timeout / lost connection mid-ingest)."""

    def __init__(self, n_rows=10, fail_at_offset=8):
        self.n_rows = n_rows
        self.fail_at_offset = fail_at_offset

    def acquire(self):
        pool = self

        class _Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def cursor(self, *a):
                return _Cursor(pool)

        return _Conn()


class _Cursor:
    def __init__(self, pool):
        self.pool = pool
        self.rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, query):
        import re

        lim = int(re.search(r"LIMIT\s+(\d+)", query).group(1))
        off = int(re.search(r"OFFSET\s+(\d+)", query).group(1))
        if off >= self.pool.fail_at_offset:
            raise RuntimeError("(1205) Lock wait timeout exceeded")
        self.rows = [{"id": i} for i in range(off, min(off + lim, self.pool.n_rows))]

    async def fetchall(self):
        return self.rows


def test_bug505_mysql_mid_table_failure_is_not_reported_as_complete(monkeypatch, uasr):
    from connectors.mysql_connector import MySQLConnector

    connector = MySQLConnector(ConnectorConfig(source_type=SourceType.MYSQL, name="t"))
    pool = _FlakyMySQLPool(n_rows=10, fail_at_offset=8)

    async def _connect():
        connector.pool = pool
        connector._is_connected = True
        return True

    async def _schema(table_name):
        return {"columns": [{"name": "id", "type": "int"}]}

    async def _disconnect():
        return True

    connector.connect = _connect
    connector.get_table_schema = _schema
    connector.disconnect = _disconnect
    _use_connector(monkeypatch, connector)
    # Pages 0-3 and 4-7 succeed, page 8+ fails. Before the fix the route returned
    # success:true, total_rows:8, truncated:false for a 10-row table.
    with pytest.raises(HTTPException) as exc:
        _ingest("mysql", batch_size=4)
    assert exc.value.status_code == 502
    assert "8" in str(exc.value.detail)  # says how many rows got through first
    assert len(uasr) == 8


def test_bug505_bigquery_execute_query_can_surface_errors():
    from connectors.bigquery_connector import BigQueryConnector

    connector = BigQueryConnector(ConnectorConfig(source_type=SourceType.BIGQUERY, name="t", database="sales"))

    class _Client:
        def query(self, sql):
            raise RuntimeError("Table \"orders\" must be qualified with a dataset")

    connector.client = _Client()
    connector._is_connected = True
    assert asyncio.run(connector.execute_query("SELECT * FROM orders")) == []
    with pytest.raises(RuntimeError, match="qualified"):
        asyncio.run(connector.execute_query("SELECT * FROM orders", raise_errors=True))


def test_bug505_bigquery_unqualified_ingest_is_not_reported_as_empty_success(monkeypatch, uasr):
    from connectors.bigquery_connector import BigQueryConnector

    connector = BigQueryConnector(ConnectorConfig(source_type=SourceType.BIGQUERY, name="t", database="sales"))

    class _Client:
        def query(self, sql):
            raise RuntimeError("Table \"orders\" must be qualified with a dataset")

    async def _connect():
        connector.client = _Client()
        connector._is_connected = True
        return True

    async def _schema(table_name):
        return {"columns": [{"name": "id", "type": "INTEGER"}]}

    connector.connect = _connect
    connector.get_table_schema = _schema
    _use_connector(monkeypatch, connector)
    with pytest.raises(HTTPException) as exc:
        _ingest("bigquery")
    assert exc.value.status_code == 502
