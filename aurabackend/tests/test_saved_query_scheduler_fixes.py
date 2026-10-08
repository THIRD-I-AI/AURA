"""Saved-query scheduler, found by the api_gateway/routers audit:

- a query saved in a workspace folder ("<tenant>::<folder>") loaded the slug of that
  whole key -- a bucket nothing was uploaded to -- so every scheduled run failed;
- a weekly schedule saved without day_of_week crashed next-run computation (a 500);
- a scheduled query that never finished stalled the scheduler for every tenant.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from api_gateway.routers import queries


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    get_storage_backend().write("acme", "sales.csv", b"region,amount\nEU,10\nUS,20\n")
    yield
    reset_storage_backend()


def test_a_query_saved_in_a_workspace_folder_reads_its_tenants_uploads(uploads):
    result = asyncio.run(queries._execute_saved_query_sql("SELECT SUM(amount) AS s FROM sales", "acme::reports"))

    assert result["success"] and result["row_count"] == 1


def test_the_bare_tenant_workspace_still_works(uploads):
    assert asyncio.run(queries._execute_saved_query_sql("SELECT * FROM sales", "acme"))["row_count"] == 2


@pytest.mark.parametrize("schedule", [
    {"interval": "weekly", "hour": 9, "minute": 0},
    {"interval": "weekly", "hour": 9, "minute": 0, "day_of_week": None},
])
def test_a_weekly_schedule_without_a_day_defaults_to_monday(schedule):
    from datetime import datetime

    nxt = queries._compute_next_run(schedule)

    assert nxt is not None
    assert datetime.fromisoformat(nxt).weekday() == 0


def test_a_scheduled_query_that_runs_too_long_is_stopped(uploads, monkeypatch):
    monkeypatch.setattr(queries, "SCHEDULED_QUERY_TIMEOUT_SECONDS", 1.0)
    runaway = "SELECT count(*) FROM range(200000) a, range(200000) b WHERE a.range + b.range = -1"
    started = time.perf_counter()

    with pytest.raises(TimeoutError, match="was stopped"):
        asyncio.run(asyncio.wait_for(queries._execute_saved_query_sql(runaway, "acme"), timeout=30))

    assert time.perf_counter() - started < 15


def test_a_due_query_runs_once_however_many_workers_tick(monkeypatch):
    # BUG-368: every uvicorn worker runs this tick against the same database, and
    # next_run_at only moved after the run finished, so a due query ran once per worker.
    import uuid
    from datetime import datetime, timedelta, timezone

    from api_gateway import persistence

    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    runs = []

    async def _slow_run(sql, workspace_id):
        runs.append(sql)
        await asyncio.sleep(0.2)
        return {"success": True, "row_count": 0, "execution_time_ms": 1}

    monkeypatch.setattr(queries, "_execute_saved_query_sql", _slow_run)
    qid = f"sq_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)

    async def _scenario():
        await persistence.insert_saved_query({
            "id": qid, "workspace_id": "ws-bug368", "name": "n", "sql": f"SELECT '{qid}'",
            "created_at": now.isoformat(), "created_ts": now.timestamp(), "updated_at": now.isoformat(),
            "schedule": {"interval": "daily", "hour": 9, "minute": 0, "enabled": True},
            "next_run_at": (now - timedelta(minutes=1)).isoformat(),
        })
        try:
            await asyncio.gather(*(queries._fire_due_saved_queries() for _ in range(4)))
        finally:
            await persistence.delete_saved_query(qid, "ws-bug368")
            if persistence._engine is not None:
                await persistence._engine.dispose()

    asyncio.run(_scenario())

    assert runs.count(f"SELECT '{qid}'") == 1
