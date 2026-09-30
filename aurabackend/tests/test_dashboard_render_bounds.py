"""BUG-234: dashboard render must be bounded.

Every tile opened its own DuckDB connection and loaded ALL of the tenant's tables,
all tiles concurrently, with no cap on tile count -- and each tile fetchall()-ed the
whole result only to keep 500 rows. One render of a 200-tile dashboard meant 200 full
copies of the tenant's data in memory at once.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import dashboards
from shared.duckdb_factory import new_connection


def _tile(i: int) -> dict:
    return {"saved_query_id": "q1", "title": f"t{i}", "chart_type": "table"}


def test_tile_count_is_capped_on_create_and_update():
    over = [_tile(i) for i in range(dashboards.MAX_DASHBOARD_TILES + 1)]
    with pytest.raises(ValidationError):
        dashboards.DashboardCreate(name="d", tiles=over)
    with pytest.raises(ValidationError):
        dashboards.DashboardUpdate(tiles=over)
    ok = dashboards.DashboardCreate(name="d", tiles=over[:-1])
    assert len(ok.tiles) == dashboards.MAX_DASHBOARD_TILES


def test_tile_fetch_is_bounded_and_reports_truncation():
    con = new_connection()
    try:
        tile = {"id": "t1", "saved_query_id": "q1", "title": "x", "chart_type": "table"}
        sq = [{"id": "q1", "name": "n", "sql": "SELECT * FROM range(100000)"}]
        out = asyncio.run(dashboards._run_tile(tile, sq, con))
    finally:
        con.close()
    assert out["row_count"] == dashboards._TILE_PREVIEW_ROWS
    assert len(out["rows"]) == dashboards._TILE_PREVIEW_ROWS
    assert out["truncated"] is True


def test_render_loads_tenant_tables_once_not_per_tile(monkeypatch):
    import shared.data_utils as data_utils

    loads = []

    async def _counting_load(con, tenant, use_llm=False):
        loads.append(tenant)
        return ""

    monkeypatch.setattr(data_utils, "build_schema_context_cached", _counting_load)
    record = {
        "id": "d1", "name": "d",
        "tiles": [dict(_tile(i), id=f"t{i}") for i in range(70)],
    }

    async def _get_dashboard(dashboard_id, workspace_id=None):
        return record

    async def _saved_queries(*a, **kw):
        return [{"id": "q1", "name": "n", "sql": "SELECT 1 AS x"}]

    monkeypatch.setattr(dashboards.persistence, "get_dashboard", _get_dashboard)
    monkeypatch.setattr(dashboards.persistence, "list_saved_queries", _saved_queries)

    from starlette.requests import Request

    req = Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/x"})
    result = asyncio.run(dashboards.render_dashboard("d1", req))
    tiles = result["tiles"] if isinstance(result, dict) else result.tiles
    assert len(loads) == 1
    # a dashboard stored before the cap existed is still capped at render time
    assert len(tiles) == 50  # MAX_DASHBOARD_TILES
