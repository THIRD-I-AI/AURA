"""Audit round 6 (connections): BUG-469, BUG-476.

The route handlers are called directly with a fake connector so no FastAPI lifespan
runs and no database is contacted.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import sys

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import connections  # noqa: E402

_N_ROWS = 10


class _UnorderedConnector:
    """A table whose scan order changes between statements, the way Postgres's
    synchronize_seqscans (or tuples moved by UPDATE/VACUUM) does when the query
    has no ORDER BY. Each statement sees the rows rotated by a different amount and
    then applies its own LIMIT/OFFSET."""

    def __init__(self) -> None:
        self.calls = 0

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> bool:
        return True

    async def profile_table(self, table_name):
        return {"rows": _N_ROWS}

    async def get_table_schema(self, table_name):
        return {"columns": [{"name": "id", "type": "integer"}]}

    async def execute_query(self, query, limit=1000):
        shift = (self.calls * 3) % _N_ROWS
        self.calls += 1
        order = [(i + shift) % _N_ROWS for i in range(_N_ROWS)]
        m_off = re.search(r"OFFSET\s+(\d+)", query, re.I)
        m_lim = re.search(r"LIMIT\s+(\d+)", query, re.I)
        offset = int(m_off.group(1)) if m_off else 0
        lim = min(limit, int(m_lim.group(1))) if m_lim else limit
        return [{"id": i} for i in order[offset:offset + lim]]


def _request(tenant: str = "tenant-a") -> Request:
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
    req.state.user = {"org_id": tenant, "sub": "user-1"}
    return req


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "local")
    from shared.storage import reset_storage_backend

    reset_storage_backend()
    connector = _UnorderedConnector()

    async def _get_connection(cid, wsid):
        return {"id": cid, "name": "pg", "type": "postgresql"}

    async def _none(*a, **k):
        return None

    async def _extra(*a, **k):
        return {}

    async def _make(conn_type, config, request):
        return connector

    async def _no_invalidate():
        return None

    monkeypatch.setattr(connections.persistence, "get_connection", _get_connection)
    monkeypatch.setattr(connections.persistence, "get_connection_secret", _none)
    monkeypatch.setattr(connections.persistence, "get_connection_extra", _extra)
    monkeypatch.setattr(connections, "_stored_connector_config", lambda *a, **k: None)
    monkeypatch.setattr(connections, "_make_connector", _make)
    import shared.data_utils as data_utils

    monkeypatch.setattr(data_utils, "invalidate_schema_cache", _no_invalidate)
    yield connector, tmp_path / "uploads"
    reset_storage_backend()


def _snapshot_ids(uploads, tenant="tenant-a"):
    import pandas as pd

    from shared.storage.base import tenant_slug

    path = uploads / tenant_slug(tenant) / "pg__orders.parquet"
    return list(pd.read_parquet(io.BytesIO(path.read_bytes()))["id"])


# ── BUG-469 ──────────────────────────────────────────────────────────


def test_bug469_sync_snapshot_has_every_row_exactly_once(wired):
    _, uploads = wired
    req = connections.ConnectionSyncRequest(table_name="orders", batch_size=4)
    out = asyncio.run(connections.sync_connection_table("c1", req, _request()))
    assert out["row_count"] == _N_ROWS
    ids = _snapshot_ids(uploads)
    # Paged LIMIT/OFFSET statements over an unordered scan overlapped and skipped
    # rows while the total still matched COUNT(*).
    assert sorted(ids) == list(range(_N_ROWS))


def test_bug469_ingest_sends_every_row_exactly_once(wired, monkeypatch):
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
                sent.extend(r["id"] for r in json["rows"])
            return _Resp()

    monkeypatch.setattr(connections.httpx, "AsyncClient", _UASR)
    import metadata_store.repository as repo_mod

    async def _no_repo():
        if False:
            yield None

    monkeypatch.setattr(repo_mod, "get_repository", _no_repo)
    req = connections.ConnectorIngestRequest(table_name="orders", batch_size=4, register_baseline=False)
    out = asyncio.run(connections.ingest_connector_data("postgresql", req, _request()))
    assert out["total_rows"] == _N_ROWS
    assert out["batches"] == 3  # still delivered to UASR in batch_size chunks
    assert sorted(sent) == list(range(_N_ROWS))


# ── BUG-476 ──────────────────────────────────────────────────────────


def test_bug476_sync_refuses_snapshot_past_tenant_quota(wired, monkeypatch):
    _, uploads = wired
    monkeypatch.setenv("AURA_TENANT_STORAGE_QUOTA_MB", "0.01")  # ~10 KB
    from shared.storage import get_storage_backend

    tdir = get_storage_backend().tenant_dir("tenant-a")
    (tdir / "big.csv").write_bytes(b"x" * 20_000)
    req = connections.ConnectionSyncRequest(table_name="orders")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(connections.sync_connection_table("c1", req, _request()))
    assert exc.value.status_code == 413
    assert not (tdir / "pg__orders.parquet").exists()


def test_bug476_resync_does_not_count_the_snapshot_it_replaces(wired, monkeypatch):
    _, uploads = wired
    monkeypatch.setenv("AURA_TENANT_STORAGE_QUOTA_MB", "0.01")
    from shared.storage import get_storage_backend

    tdir = get_storage_backend().tenant_dir("tenant-a")
    # An old snapshot of the same name that alone would push the total over.
    (tdir / "pg__orders.parquet").write_bytes(b"x" * 9_000)
    req = connections.ConnectionSyncRequest(table_name="orders")
    out = asyncio.run(connections.sync_connection_table("c1", req, _request()))
    assert out["row_count"] == _N_ROWS
    assert sorted(_snapshot_ids(uploads)) == list(range(_N_ROWS))
