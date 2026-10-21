"""Audit round 6 (connections): BUG-469, BUG-476.

The route handlers are called directly with a fake connector so no FastAPI lifespan
runs and no database is contacted.
"""
from __future__ import annotations

import asyncio
import contextlib
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
        self.largest_fetch = 0  # most rows handed back by any one read

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> bool:
        return True

    async def profile_table(self, table_name):
        return {"rows": _N_ROWS}

    async def get_table_schema(self, table_name):
        return {"columns": [{"name": "id", "type": "integer"}]}

    def _scan_order(self):
        shift = (self.calls * 3) % _N_ROWS
        self.calls += 1
        return [(i + shift) % _N_ROWS for i in range(_N_ROWS)]

    async def execute_query(self, query, limit=1000):
        order = self._scan_order()
        m_off = re.search(r"OFFSET\s+(\d+)", query, re.I)
        m_lim = re.search(r"LIMIT\s+(\d+)", query, re.I)
        offset = int(m_off.group(1)) if m_off else 0
        lim = min(limit, int(m_lim.group(1))) if m_lim else limit
        rows = [{"id": i} for i in order[offset:offset + lim]]
        self.largest_fetch = max(self.largest_fetch, len(rows))
        return rows

    async def stream_query(self, query, batch_size, max_rows):
        # One statement, so one scan order, handed out a batch at a time.
        order = self._scan_order()[:max_rows]
        for start in range(0, len(order), batch_size):
            batch = [{"id": i} for i in order[start:start + batch_size]]
            self.largest_fetch = max(self.largest_fetch, len(batch))
            yield batch


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


def _fake_uasr(monkeypatch, status_code=200):
    """Route UASR calls to a recorder; returns the ids sent to /uasr/ingest."""
    sent = []

    class _Resp:
        def __init__(self):
            self.status_code = status_code

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
    return sent


def _ingest(batch_size=4):
    req = connections.ConnectorIngestRequest(table_name="orders", batch_size=batch_size, register_baseline=False)
    return asyncio.run(connections.ingest_connector_data("postgresql", req, _request()))


def test_bug469_ingest_sends_every_row_exactly_once(wired, monkeypatch):
    sent = _fake_uasr(monkeypatch)
    out = _ingest()
    assert out["total_rows"] == _N_ROWS
    assert out["batches"] == 3  # still delivered to UASR in batch_size chunks
    assert sorted(sent) == list(range(_N_ROWS))


def test_bug469_ingest_never_holds_more_than_one_batch(wired, monkeypatch):
    connector, _ = wired
    _fake_uasr(monkeypatch)
    # No max_rows, so row_cap is the 2M ceiling: a single bulk read would pull the
    # whole table (here 10 rows, in prod up to 2M) into the one gateway worker.
    out = _ingest(batch_size=4)
    assert out["total_rows"] == _N_ROWS
    assert 0 < connector.largest_fetch <= 4


def test_bug469_ingest_without_streaming_pages_one_batch_at_a_time(wired, monkeypatch):
    connector, _ = wired
    connector.stream_query = None  # a connector that cannot stream one statement
    seen = []
    real = connector.execute_query

    async def _bounded(query, limit=1000):
        assert re.search(r"LIMIT\s+\d+", query), f"unbounded read: {query}"
        seen.append(query)
        return await real(query, limit=limit)

    connector.execute_query = _bounded
    _fake_uasr(monkeypatch)
    _ingest(batch_size=4)
    assert seen and connector.largest_fetch <= 4


# PostgreSQLConnector.stream_query against a fake asyncpg pool (no database here).


class _FakeCursor:
    def __init__(self, rows, fetch_sizes):
        self._rows = list(rows)
        self._sizes = fetch_sizes

    async def fetch(self, n):
        self._sizes.append(n)
        batch, self._rows = self._rows[:n], self._rows[n:]
        return batch


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    def transaction(self, **kw):
        self._pool.transactions.append(kw)
        return _FakeTx()

    async def cursor(self, query):
        self._pool.cursor_queries.append(query)
        return _FakeCursor([{"id": i} for i in range(_N_ROWS)], self._pool.fetch_sizes)

    async def fetch(self, *a, **k):
        return []


class _FakePool:
    def __init__(self):
        self.outstanding = 0
        self.outstanding_at_close = None
        self.transactions, self.cursor_queries, self.fetch_sizes = [], [], []

    @contextlib.asynccontextmanager
    async def acquire(self):
        self.outstanding += 1
        try:
            yield _FakeConn(self)
        finally:
            self.outstanding -= 1

    async def close(self):
        # asyncpg's Pool.close() waits for every acquired connection to be released.
        self.outstanding_at_close = self.outstanding


def _pg_connector():
    from connectors.base import ConnectorConfig, SourceType
    from connectors.postgresql_connector import PostgreSQLConnector

    pg = PostgreSQLConnector(ConnectorConfig(source_type=SourceType.POSTGRESQL, name="t"))
    pg.pool = _FakePool()
    pg._is_connected = True
    return pg


def test_bug469_postgres_stream_reads_one_repeatable_read_cursor_in_batches():
    pg = _pg_connector()

    async def _collect():
        return [b async for b in pg.stream_query("SELECT * FROM orders", 4, 7)]

    batches = asyncio.run(_collect())
    assert [len(b) for b in batches] == [4, 3]  # max_rows=7 stops the read
    assert pg.pool.cursor_queries == ["SELECT * FROM orders"]  # one statement
    assert pg.pool.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert max(pg.pool.fetch_sizes) <= 4
    assert pg.pool.outstanding == 0


def test_bug469_ingest_releases_cursor_before_disconnect_on_uasr_error(wired, monkeypatch):
    pg = _pg_connector()

    async def _connect():
        return True

    pg.connect = _connect

    async def _make(*a, **k):
        return pg

    monkeypatch.setattr(connections, "_make_connector", _make)
    _fake_uasr(monkeypatch, status_code=500)
    with pytest.raises(HTTPException) as exc:
        _ingest()
    assert exc.value.status_code == 502
    # A still-open cursor here would make the real pool.close() wait forever.
    assert pg.pool.outstanding_at_close == 0


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
