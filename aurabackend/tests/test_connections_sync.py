"""Connections -> chat "sync to chat" bridge (POST /connections/{id}/sync).

Materializes a connector table as a parquet snapshot in the tenant's upload
directory, so the existing NL-to-SQL chat pipeline (which only scans that
directory — shared/data_utils.py::build_schema_context_cached) can query it
with zero changes to chat.py, the LangGraph orchestrator, or the DPC
verifier. Uses a real file-backed DuckDBConnector (no external service
required, and no mocking — DuckDB is a local, fully-functional connector) as
the "connection" under test, matching the pattern in tests/test_connectors.py.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

V1 = "/api/v1"
_PASSWORD = None  # DuckDB file connector needs no credential


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_engine():
    """Same reset as test_connections_security.py: pytest-asyncio gives each
    test its own event loop while the module-level engine stays bound to the
    first, so the second async test in a session deadlocks."""
    from api_gateway import persistence
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    yield


@pytest.fixture
def duckdb_source(tmp_path):
    """A real, file-backed DuckDB database with one table — connect()ing to
    it a second time (as the sync endpoint does, inside the request) still
    sees the data, unlike a ':memory:' connector."""
    pytest.importorskip("duckdb", reason="duckdb not installed")
    import duckdb

    db_path = tmp_path / "source.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE customers (id INTEGER, name VARCHAR, spend DOUBLE)")
    con.execute(
        "INSERT INTO customers VALUES (1,'Ada',120.5),(2,'Grace',80.0),(3,'Lin',300.25)"
    )
    con.close()
    return str(db_path)


async def _register_connection(workspace_id: str, database_path: str, name: str = "duck-src") -> dict:
    from api_gateway import persistence

    now = datetime.now(timezone.utc)
    record = {
        "id": f"conn_{uuid.uuid4().hex[:12]}",
        "workspace_id": workspace_id,
        "name": name,
        "type": "duckdb",
        "host": None, "port": None, "database": database_path,
        "username": None, "ssl": False,
        "created_at": now.isoformat(), "created_ts": now.timestamp(),
        "updated_at": now.isoformat(),
    }
    return await persistence.insert_connection(record, _PASSWORD)


@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path, monkeypatch):
    """Point the storage backend at a throwaway root so this test's synced
    parquet files never land in (or collide with) the shared dev upload
    dir or another test's tenant bucket."""
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    yield
    reset_storage_backend()


@pytest.mark.asyncio
async def test_sync_writes_readable_parquet_with_right_row_count(client, duckdb_source, tmp_path):
    conn = await _register_connection("default", duckdb_source)

    resp = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["connection_id"] == conn["id"]
    assert data["table_name"] == "customers"
    assert data["row_count"] == 3
    assert data["file_name"].endswith(".parquet")
    assert "duck-src" in data["file_name"]

    upload_root = os.path.join(str(tmp_path / "uploads"), "default")
    written = os.path.join(upload_root, data["file_name"])
    assert os.path.exists(written)

    import pandas as pd
    df = pd.read_parquet(written)
    assert len(df) == 3
    assert set(df.columns) == {"id", "name", "spend"}
    assert set(df["name"]) == {"Ada", "Grace", "Lin"}


@pytest.mark.asyncio
async def test_resync_overwrites_not_duplicates(client, duckdb_source, tmp_path):
    conn = await _register_connection("default", duckdb_source)

    first = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})
    second = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["file_name"] == second.json()["file_name"]

    upload_root = os.path.join(str(tmp_path / "uploads"), "default")
    parquet_files = [f for f in os.listdir(upload_root) if f.endswith(".parquet")]
    assert parquet_files.count(second.json()["file_name"]) == 1
    assert len(parquet_files) == 1


@pytest.mark.asyncio
async def test_invalid_table_name_rejected(client, duckdb_source):
    conn = await _register_connection("default", duckdb_source)

    resp = client.post(
        f"{V1}/connections/{conn['id']}/sync",
        json={"table_name": "customers; DROP TABLE customers; --"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_nonexistent_connection_404(client):
    resp = client.post(f"{V1}/connections/does-not-exist/sync", json={"table_name": "customers"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_another_workspaces_connection_404s(client, duckdb_source):
    """A connection registered under a different workspace must 404, not
    leak via a 403 or succeed — same isolation boundary as
    test_connections_security.py::test_connections_do_not_leak_across_tenants."""
    conn = await _register_connection("ws-other", duckdb_source)

    # Default (unauthenticated, header-less) request resolves to the
    # 'default' workspace, which never owned this connection.
    resp = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_synced_table_is_picked_up_by_chat_schema_context(client, duckdb_source, tmp_path):
    """Integration proof of the core design claim: once synced, the table
    shows up in build_schema_context_cached's scan with ZERO chat-side
    changes — the same function chat.py calls on every request."""
    conn = await _register_connection("default", duckdb_source)

    resp = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})
    assert resp.status_code == 200, resp.text

    import shared.duckdb_factory as duckdb_factory
    from shared.data_utils import build_schema_context_cached

    con = duckdb_factory.new_connection()
    try:
        result = await build_schema_context_cached(con, None, use_llm=False)
    finally:
        con.close()

    # data_utils names loaded tables after the file stem, not the raw
    # connector table name — so this proves the synced parquet was
    # discovered and loaded purely by scanning the upload dir.
    assert any("customers" in t for t in result["tables"])
    assert "spend" in result["context_text"]
