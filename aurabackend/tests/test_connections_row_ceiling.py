"""BUG-193: sync and ingest must not pull an unbounded number of rows into memory.

Reuses the fixtures of tests/test_connections_sync.py (real file-backed DuckDB source).

Original module doc:
Connections -> chat "sync to chat" bridge (POST /connections/{id}/sync).

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
async def test_sync_without_row_estimate_refuses_a_table_over_the_ceiling(client, duckdb_source, monkeypatch):
    """No usable COUNT(*) estimate and no max_rows used to leave the paging loop unbounded."""
    from api_gateway.routers import connections as mod
    from connectors.duckdb_connector import DuckDBConnector

    async def _no_estimate(self, table):
        raise RuntimeError("count unavailable")

    monkeypatch.setattr(DuckDBConnector, "profile_table", _no_estimate)
    monkeypatch.setattr(mod, "_SYNC_ROW_CEILING", 2)  # the source table has 3 rows
    conn = await _register_connection("default", duckdb_source)

    resp = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})
    assert resp.status_code == 400, resp.text
    assert "ceiling" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sync_max_rows_above_the_ceiling_is_rejected(client, duckdb_source, monkeypatch):
    from api_gateway.routers import connections as mod

    monkeypatch.setattr(mod, "_SYNC_ROW_CEILING", 2)
    conn = await _register_connection("default", duckdb_source)

    resp = client.post(
        f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers", "max_rows": 3}
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_sync_within_the_ceiling_still_works(client, duckdb_source, monkeypatch):
    from api_gateway.routers import connections as mod

    monkeypatch.setattr(mod, "_SYNC_ROW_CEILING", 3)  # exactly the table size
    conn = await _register_connection("default", duckdb_source)

    resp = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 3


def test_ingest_max_rows_above_the_ceiling_is_rejected(client, duckdb_source, monkeypatch):
    from api_gateway.routers import connections as mod

    monkeypatch.setattr(mod, "_SYNC_ROW_CEILING", 2)
    resp = client.post(
        f"{V1}/connectors/duckdb/ingest",
        json={
            "connector_config": {"database": duckdb_source},
            "table_name": "customers",
            "max_rows": 3,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "max_rows" in resp.json()["detail"]
