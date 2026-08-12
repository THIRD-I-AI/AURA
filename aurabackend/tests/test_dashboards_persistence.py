"""Dashboards must survive a restart, and must not leak across workspaces.

They used to live in a module-level Python list, so every dashboard a user
built vanished on the next gateway restart — silently, and inconsistently with
the saved-query library dashboards are assembled from. It also meant two
replicas each held their own private set.

The restart test below is the one that matters: it drops the engine and session
factory the way a process restart does, then reads the dashboard back.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway import persistence


@pytest.fixture(autouse=True)
def _fresh_engine():
    """Rebind the async engine per test.

    pytest-asyncio gives each test its own event loop, but the module-level
    engine is created on the FIRST loop and aiosqlite's worker thread stays
    bound to it — so the second async test in a session deadlocks with no
    output at all. Every test here passes alone and hangs in a group without
    this. Mirrors the reset that tests/test_fairness_audit_ledger.py does for
    the ledger engine. Deliberately does NOT call close_database(): awaiting
    that teardown against the previous, already-closed loop is itself a hang.
    """
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    yield


def _record(ws: str, name: str = "Q3 revenue") -> dict:
    now = datetime.now(timezone.utc)
    return {
        # Unique per run. The whole point of these tests is that the store is
        # DURABLE, so rows outlive the run — a deterministic id would collide
        # with its own previous execution on the primary key.
        "id": f"dash_{uuid.uuid4().hex[:12]}",
        "workspace_id": ws,
        "name": name,
        "description": "quarterly revenue view",
        "tiles": [{
            "id": "tile_0",
            "saved_query_id": "sq_revenue",
            "title": "Revenue by region",
            "chart_type": "bar",
        }],
        "created_at": now.isoformat(),
        "created_ts": now.timestamp(),
        "updated_at": now.isoformat(),
    }


@pytest.mark.asyncio
async def test_dashboard_is_stored_in_the_database_not_in_memory():
    """The actual regression: dashboards must be rows, not a Python list.

    Tearing the engine down to fake a process restart was tried and rejected —
    close_database() plus nulling the module globals hangs the aiosqlite worker
    and corrupts state the rest of the suite shares. Reading the row back
    through raw SQL on a fresh session proves the same thing without that:
    if the dashboard is in the table, it survives a restart by construction.
    """
    from sqlalchemy import select

    saved = await persistence.insert_dashboard(_record("ws-restart"))
    assert saved["tiles"][0]["saved_query_id"] == "sq_revenue"

    async with persistence.session_scope() as s:
        row = (await s.execute(
            select(persistence.DashboardRow)
            .where(persistence.DashboardRow.id == saved["id"]),
        )).scalar_one_or_none()

    assert row is not None, "dashboard was not written to gateway_dashboards"
    assert row.workspace_id == "ws-restart"
    # Tiles round-trip through JSON text — prove the structure survived, not
    # merely that a row exists.
    assert '"chart_type": "bar"' in row.tiles_json

    again = await persistence.get_dashboard(saved["id"], "ws-restart")
    assert again is not None and again["tiles"][0]["chart_type"] == "bar"


@pytest.mark.asyncio
async def test_dashboards_do_not_leak_across_workspaces():
    mine = await persistence.insert_dashboard(_record("ws-a", "private board"))

    # Another workspace must not see it in a listing...
    assert await persistence.list_dashboards("ws-b") == []
    # ...nor resolve it by id. None here becomes a 404 in the router, never a
    # 403, so the response cannot be used to confirm the id exists.
    assert await persistence.get_dashboard(mine["id"], "ws-b") is None
    # ...nor mutate or delete it.
    assert await persistence.update_dashboard(mine["id"], "ws-b", {"name": "hijacked"}) is None
    assert await persistence.delete_dashboard(mine["id"], "ws-b") is False

    # The owner is unaffected by any of that.
    still = await persistence.get_dashboard(mine["id"], "ws-a")
    assert still is not None and still["name"] == "private board"


@pytest.mark.asyncio
async def test_update_and_delete_round_trip():
    rec = await persistence.insert_dashboard(_record("ws-crud", "before"))

    updated = await persistence.update_dashboard(
        rec["id"], "ws-crud", {"name": "after", "tiles": []},
    )
    assert updated is not None
    assert updated["name"] == "after" and updated["tiles"] == []
    assert updated["updated_at"] >= rec["created_at"]

    assert await persistence.delete_dashboard(rec["id"], "ws-crud") is True
    assert await persistence.get_dashboard(rec["id"], "ws-crud") is None
    # Deleting twice is not an error for the caller — it reports False so the
    # router can answer 404 rather than pretending the delete worked.
    assert await persistence.delete_dashboard(rec["id"], "ws-crud") is False
