"""Audit round 6, queries/pipelines area: BUG-468, BUG-470, BUG-471, BUG-475."""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api_gateway.routers import queries
from pipeline.engine import PipelineEngine
from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)

_UNITS = {"KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}


def _memory_limit_bytes(con) -> float:
    value = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    number, unit = re.fullmatch(r"([\d.]+) (\w+)", value).groups()
    return float(number) * _UNITS[unit]


# ── BUG-468 ───────────────────────────────────────────────────────────────

def test_a_new_connection_has_a_bounded_memory_limit():
    from shared.duckdb_factory import new_connection

    con = new_connection()
    try:
        assert _memory_limit_bytes(con) <= 256 * 2**20
    finally:
        con.close()


def test_user_sql_cannot_raise_the_memory_limit_after_lockdown():
    import duckdb

    from shared.duckdb_factory import lock_down_connection, new_connection

    con = new_connection()
    try:
        lock_down_connection(con)
        with pytest.raises(duckdb.Error):
            con.execute("SET memory_limit='16GB'")
        assert _memory_limit_bytes(con) <= 256 * 2**20
    finally:
        con.close()


# ── BUG-470 ───────────────────────────────────────────────────────────────

URL = "/api/v1/execute/query"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")  # local test host (BUG-377)
    from agents.base import AgentResult, AgentStatus
    from agents.specialists import analysis_agent
    from api_gateway.main import app

    async def _no_llm(self, ctx):
        return AgentResult(status=AgentStatus.FAILED, error="no provider in tests")

    monkeypatch.setattr(analysis_agent.AnalysisAgent, "execute", _no_llm)
    return TestClient(app)


def _free_local_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_a_failed_connect_is_reported_as_a_failure(client):
    # A real MySQLConnector pointed at a local port nobody listens on: connect() returns False.
    resp = client.post(URL, json={
        "query": "SELECT 1", "connector_type": "mysql",
        "connector_config": {"host": "127.0.0.1", "port": _free_local_port(), "database": "shop"},
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["error"]


def test_a_failed_statement_is_reported_as_a_failure(client, monkeypatch):
    class FailingStatementConnector:  # behaves as MySQLConnector does for a bad statement
        async def connect(self):
            return True

        async def disconnect(self):
            return True

        async def execute_query(self, query, limit=1000, raise_errors=False):
            if raise_errors:
                raise RuntimeError("Table 'shop.orderz' doesn't exist")
            return []

    async def _open(ctype, cfg, *, files_root, build=None):
        return FailingStatementConnector()

    monkeypatch.setattr(queries, "open_connector", _open)

    resp = client.post(URL, json={"query": "SELECT * FROM orderz", "connector_type": "mysql",
                                  "connector_config": {"host": "db.example", "database": "shop"}})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["error"]


# ── BUG-471 ───────────────────────────────────────────────────────────────

def test_a_schedule_changed_during_a_run_keeps_its_next_run_at(monkeypatch):
    from api_gateway import persistence

    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    qid = f"sq_{uuid.uuid4().hex[:10]}"
    ws = "ws-bug471"
    now = datetime.now(timezone.utc)
    users_next = (now + timedelta(minutes=30)).isoformat()

    async def _run_while_user_edits(sql, workspace_id):
        # The PUT /saved-queries/{id}/schedule that lands while the query is running.
        await persistence.update_saved_query(qid, ws, {
            "schedule": {"interval": "hourly", "minute": 30, "enabled": True},
            "next_run_at": users_next,
        })
        return {"success": True, "row_count": 0, "execution_time_ms": 1}

    monkeypatch.setattr(queries, "_execute_saved_query_sql", _run_while_user_edits)

    async def _scenario():
        await persistence.insert_saved_query({
            "id": qid, "workspace_id": ws, "name": "n", "sql": "SELECT 1",
            "created_at": now.isoformat(), "created_ts": now.timestamp(), "updated_at": now.isoformat(),
            "schedule": {"interval": "daily", "hour": 9, "minute": 0, "enabled": True},
            "next_run_at": (now - timedelta(minutes=1)).isoformat(),
        })
        try:
            await queries._fire_due_saved_queries()
            return await persistence.get_saved_query(qid, ws)
        finally:
            await persistence.delete_saved_query(qid, ws)
            if persistence._engine is not None:
                await persistence._engine.dispose()

    record = asyncio.run(_scenario())

    assert record["schedule"]["interval"] == "hourly"
    assert record["next_run_at"] == users_next
    assert record["last_run_at"]


# ── BUG-475 ───────────────────────────────────────────────────────────────

CSV = b"region,amount\nEast,1\nWest,2\nNorth,3\nO'Hare,4\n"


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    get_storage_backend().write("acme", "orders.csv", CSV)
    yield
    reset_storage_backend()


async def _filter(operator: str, value):
    pipeline = Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="orders.csv"),
        steps=[ProcessingStep(type=StepType.FILTER,
                              config={"column": "region", "operator": operator, "value": value})],
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    return await PipelineEngine().execute(pipeline, preview_only=True, tenant="acme")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["East,West", "East, West", ["East", "West"]])
async def test_filter_in_keeps_the_listed_values(uploads, value):
    run = await _filter("IN", value)

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert sorted(r["region"] for r in run.preview_data) == ["East", "West"]


@pytest.mark.asyncio
async def test_filter_not_in_drops_the_listed_values_and_quotes_safely(uploads):
    run = await _filter("not in", ["East", "O'Hare"])

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert sorted(r["region"] for r in run.preview_data) == ["North", "West"]


@pytest.mark.asyncio
async def test_filter_in_with_no_values_is_a_misconfiguration(uploads):
    run = await _filter("IN", [])

    assert run.status == PipelineStatus.FAILED
    assert "no values" in (run.error or "")
