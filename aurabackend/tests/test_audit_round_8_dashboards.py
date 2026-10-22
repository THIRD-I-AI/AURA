"""Audit round 8, dashboards / saved-query area: BUG-491, BUG-492, BUG-499.

(BUG-493 is a frontend bug; its regression test is
frontend/src/workbench/panels/__tests__/DashboardsPanel.manage.test.tsx.)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway import persistence
from api_gateway.routers import dashboards, queries
from shared.duckdb_factory import new_connection


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})


def _serialise_like_fastapi(payload: dict) -> bytes:
    # What FastAPI does to a dict a route returns: jsonable_encoder, then
    # Starlette's json.dumps(allow_nan=False). Both steps run after the route
    # body (and _run_tile's try/except) has returned.
    return JSONResponse(content=jsonable_encoder(payload)).body


# ── BUG-491 ─────────────────────────────────────────────────────────

def test_bug_491_nan_inf_and_non_utf8_blob_cells_serialise():
    sql = (
        "SELECT 'nan'::DOUBLE AS a, 'inf'::DOUBLE AS b, '-inf'::DOUBLE AS c, "
        r"'\xFF\xFE'::BLOB AS blob_bad, 'ok'::BLOB AS blob_ok, "
        "[1.5, 'nan'::DOUBLE] AS l, {'x': 'inf'::DOUBLE, 'y': 2} AS s, 3.25 AS fine"
    )
    con = new_connection()
    try:
        tile = {"id": "t1", "saved_query_id": "q1", "title": "x", "chart_type": "table"}
        out = asyncio.run(dashboards._run_tile(tile, [{"id": "q1", "name": "n", "sql": sql}], con))
    finally:
        con.close()
    assert out["status"] == "success"
    body = _serialise_like_fastapi(out)  # raised ValueError / UnicodeDecodeError before the fix
    assert b'"rows":[[null,null,null,"\\\\xff\\\\xfe","ok",[1.5,null],{"x":null,"y":2},3.25]]' in body


# ── BUG-492 / BUG-499: same-millisecond creates ─────────────────────

class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 21, 12, 0, 0, 123000, tzinfo=tz)


@pytest.fixture
def _tmp_gateway_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'gw.db').as_posix()}")
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    yield
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False


def _run_with_db(coro_factory):
    async def _go():
        try:
            return await coro_factory()
        finally:
            await persistence.close_database()
    return asyncio.run(_go())


def test_bug_492_two_dashboards_created_in_the_same_millisecond_both_persist(_tmp_gateway_db, monkeypatch):
    monkeypatch.setattr(dashboards, "datetime", _FrozenDatetime)

    async def _two_creates():
        a = await dashboards.create_dashboard(dashboards.DashboardCreate(name="A"), _request())
        b = await dashboards.create_dashboard(dashboards.DashboardCreate(name="B"), _request())
        listed = await persistence.list_dashboards(dashboards.DEFAULT_WORKSPACE_ID)
        return a, b, listed

    a, b, listed = _run_with_db(_two_creates)  # IntegrityError on the 2nd insert before the fix
    assert a["dashboard"]["id"] != b["dashboard"]["id"]
    assert {d["name"] for d in listed} == {"A", "B"}


def test_bug_499_two_saved_queries_created_in_the_same_millisecond_both_persist(_tmp_gateway_db, monkeypatch):
    monkeypatch.setattr(queries, "datetime", _FrozenDatetime)
    import shared.tasks

    # The lineage-edge cache write is fire-and-forget; it is not what this
    # test is about and must not outlive the event loop.
    monkeypatch.setattr(shared.tasks, "fire_and_forget", lambda coro, name=None: coro.close())

    async def _two_creates():
        a = await queries.create_saved_query(queries.SavedQueryCreate(name="A", sql="SELECT 1"), _request())
        b = await queries.create_saved_query(queries.SavedQueryCreate(name="B", sql="SELECT 2"), _request())
        listed = await persistence.list_saved_queries(queries.DEFAULT_WORKSPACE_ID)
        return a, b, listed

    a, b, listed = _run_with_db(_two_creates)  # IntegrityError on the 2nd insert before the fix
    assert a["query"]["id"] != b["query"]["id"]
    assert {q["name"] for q in listed} == {"A", "B"}
