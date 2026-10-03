"""BUG-329: GET /dashboard/stats returned one process-wide query count and row total
as the caller's own, so every tenant saw the sum of every tenant's chat activity."""
from __future__ import annotations

import asyncio

import pytest

from api_gateway.routers import queries


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch):
    monkeypatch.setattr(queries, "_dashboard_counters", {})


def test_a_tenants_queries_count_only_against_its_own_workspace():
    async def activity():
        for _ in range(5):
            await queries.track_query("q", "SELECT 1", "success", 400_000, 3.0, "tenant-a")
        await queries.track_query("q", "SELECT 1", "error", 0, 1.0, "tenant-a")
        await queries.track_query("q", "SELECT 2", "success", 7, 2.0, "tenant-b")

    asyncio.run(activity())

    assert queries._dashboard_counters["tenant-a"] == {"queries_run": 6, "total_rows": 2_000_000}
    assert queries._dashboard_counters["tenant-b"] == {"queries_run": 1, "total_rows": 7}


def test_the_stats_endpoint_reports_only_the_callers_workspace():
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    from shared.cache import dashboard_cache

    client = TestClient(app)
    headers = {"X-Workspace-Id": "fresh-workspace"}

    # total_rows also counts rows in the workspace's uploaded files, which other tests
    # may have left in the shared upload root -- so compare against this workspace's
    # own baseline rather than against zero.
    asyncio.run(dashboard_cache.clear())
    before = client.get("/api/v1/dashboard/stats", headers=headers).json()

    queries._dashboard_counters["someone-else"] = {"queries_run": 500, "total_rows": 2_000_000}
    asyncio.run(dashboard_cache.clear())
    resp = client.get("/api/v1/dashboard/stats", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queries_run"] == 0
    assert body["total_rows"] == before["total_rows"]
