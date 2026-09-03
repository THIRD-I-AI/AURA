"""
BUG-039 — gateway_query_history had no workspace_id column, so
GET /query-history could read (and POST /query-history could write) any
other tenant's executed SQL/prompts. Regression coverage for the fix.

Uses the same isolated-SQLite ``gateway_db`` fixture as
``test_api_gateway_persistence.py`` (the persistence layer's own contract
tests) and the same fake-``Request``-plus-route-function-call pattern as
``test_evolution.py``'s ``TestEvolutionAPITenantIsolation`` (the closest
precedent for a persistence-backed, tenant-scoped route in this repo).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from api_gateway import persistence
from api_gateway.routers import chat as chat_mod
from api_gateway.routers import queries as queries_mod
from api_gateway.routers.workspaces import current_workspace_id


@pytest_asyncio.fixture
async def gateway_db(tmp_path, monkeypatch):
    """Fresh SQLite-backed gateway persistence per test — same pattern as
    ``test_api_gateway_persistence.py``'s fixture of the same name."""
    db_path = tmp_path / f"gw_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    persistence._engine = None
    persistence._session_factory = None
    await persistence.init_database()
    yield
    await persistence.close_database()


class _FakeRequest:
    """Minimal stand-in for FastAPI's ``Request`` — carries only what
    ``current_workspace_id`` reads: ``state.user`` (the verified JWT
    principal) and ``headers`` (the client-selectable folder override,
    deliberately never trusted as the tenant boundary)."""

    def __init__(self, org_id: str):
        class _State:
            pass
        self.state = _State()
        self.state.user = {"org_id": org_id}
        self.headers = {}


def _history_record(i: int, workspace_id: str) -> dict:
    return {
        "id": f"q_{workspace_id}_{i}",
        "workspace_id": workspace_id,
        "prompt": f"prompt {i} for {workspace_id}",
        "sql": f"SELECT {i}",
        "status": "success",
        "rows": i,
        "executionTime": 1.0,
    }


@pytest.mark.asyncio
async def test_list_query_history_scoped_to_caller_workspace(gateway_db) -> None:
    """orgA's history is invisible to orgB and vice versa."""
    await persistence.insert_query_history(_history_record(1, "orgA"))
    await persistence.insert_query_history(_history_record(2, "orgB"))

    result_a = await queries_mod.get_query_history(_FakeRequest("orgA"))
    result_b = await queries_mod.get_query_history(_FakeRequest("orgB"))

    assert [q["id"] for q in result_a["queries"]] == ["q_orgA_1"]
    assert [q["id"] for q in result_b["queries"]] == ["q_orgB_2"]
    assert result_a["total"] == 1
    assert result_b["total"] == 1


@pytest.mark.asyncio
async def test_save_query_history_stamps_callers_workspace_not_client_payload(
    gateway_db,
) -> None:
    """POST /query-history must stamp the CALLER's workspace, never a
    client-supplied one — otherwise a caller could write a fabricated,
    unowned entry directly into another tenant's history."""
    payload = {
        "id": "spoofed_1",
        "workspace_id": "orgB",  # spoofed — must be ignored
        "prompt": "select salaries",
        "sql": "SELECT * FROM payroll",
        "status": "success",
        "rows": 3,
        "executionTime": 5.0,
    }

    resp = await queries_mod.save_query_history(payload, _FakeRequest("orgA"))
    assert resp["success"] is True

    seen_by_a = await persistence.list_query_history(workspace_id="orgA")
    seen_by_b = await persistence.list_query_history(workspace_id="orgB")

    assert [q["id"] for q in seen_by_a] == ["spoofed_1"]
    assert seen_by_b == [], "client-supplied workspace_id won over the caller's real tenant"


@pytest.mark.asyncio
async def test_track_query_from_chat_stamps_caller_workspace(gateway_db) -> None:
    """chat.py's call site resolves the workspace from the verified
    http_request (never a client field) before handing it to
    ``track_query`` — this is the path every executed chat query goes
    through on its way into gateway_query_history."""
    wsid = current_workspace_id(_FakeRequest("orgA"))
    await chat_mod._track_query(
        "show me revenue", "SELECT * FROM revenue", "success", 10, 42.0, wsid,
    )

    rows_a = await persistence.list_query_history(workspace_id="orgA")
    rows_b = await persistence.list_query_history(workspace_id="orgB")

    assert len(rows_a) == 1
    assert rows_a[0]["sql"] == "SELECT * FROM revenue"
    assert rows_b == []
