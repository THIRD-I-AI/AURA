"""
Dashboards Router
=================
First-class dashboard objects composed of tiles that reference saved
queries. Tiles render by executing the referenced saved query's SQL
against the uploaded-file DuckDB (same path the saved-query scheduler
uses).

Storage is the gateway persistence layer (``persistence.DashboardRow``),
so dashboards survive restarts and multiple replicas — the same durability
the saved-query library they are built from already had. No drag/drop
layout yet; tiles render in order.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway import persistence
from api_gateway.routers.workspaces import (
    DEFAULT_WORKSPACE_ID,
    _request_tenant,
    current_workspace_id,
    tenant_upload_dir,
)
from shared.error_handler import sanitize_error
from shared.logging_config import get_logger

logger = get_logger("aura.api_gateway.dashboards")

router = APIRouter(tags=["Dashboards"])


# ── Storage ─────────────────────────────────────────────────────────
# Dashboards live in the gateway's SQLAlchemy layer (persistence.DashboardRow),
# not a module-level list. The list meant every dashboard a user built was lost
# on restart — silently, and inconsistently with the saved-query library that
# dashboards are assembled from — and it could not survive replicas > 1, since
# each pod held its own copy. The per-workspace cap now lives in persistence.


# ── Models ──────────────────────────────────────────────────────────

class DashboardTileInput(BaseModel):
    saved_query_id: str
    title: Optional[str] = None
    chart_type: Optional[str] = Field(
        None, description="table | bar | line | pie | kpi"
    )


class DashboardCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    tiles: List[DashboardTileInput] = Field(default_factory=list)


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tiles: Optional[List[DashboardTileInput]] = None


# ── Helpers ─────────────────────────────────────────────────────────

def _tile_to_record(tile: DashboardTileInput, index: int) -> Dict[str, Any]:
    return {
        "id": f"tile_{index}_{int(time.time() * 1000)}",
        "saved_query_id": tile.saved_query_id,
        "title": (tile.title or "").strip() or None,
        "chart_type": tile.chart_type or "table",
    }


# ── CRUD endpoints ──────────────────────────────────────────────────

@router.get("/dashboards")
async def list_dashboards(request: Request):
    """Return dashboards for the caller's workspace, newest-first."""
    wsid = current_workspace_id(request)
    records = await persistence.list_dashboards(wsid)
    return {"success": True, "dashboards": records, "total": len(records)}


@router.post("/dashboards")
async def create_dashboard(payload: DashboardCreate, request: Request):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    wsid = current_workspace_id(request)
    ts = datetime.now()
    record = {
        "id": f"dash_{int(ts.timestamp() * 1000)}",
        "workspace_id": wsid,
        "name": name,
        "description": (payload.description or "").strip() or None,
        "tiles": [_tile_to_record(t, i) for i, t in enumerate(payload.tiles)],
        "created_at": ts.isoformat(),
        "created_ts": ts.timestamp(),
        "updated_at": ts.isoformat(),
    }
    saved = await persistence.insert_dashboard(record)
    return {"success": True, "dashboard": saved}


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str, request: Request):
    wsid = current_workspace_id(request)
    record = await persistence.get_dashboard(dashboard_id, wsid)
    if record is None:
        # 404 rather than 403 for another workspace's id: a 403 would confirm
        # the dashboard exists and turn this into an existence oracle.
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return {"success": True, "dashboard": record}


@router.patch("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: str, payload: DashboardUpdate, request: Request):
    wsid = current_workspace_id(request)
    fields: Dict[str, Any] = {}
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        fields["name"] = new_name
    if payload.description is not None:
        fields["description"] = payload.description.strip() or None
    if payload.tiles is not None:
        fields["tiles"] = [_tile_to_record(t, i) for i, t in enumerate(payload.tiles)]

    record = await persistence.update_dashboard(dashboard_id, wsid, fields)
    if record is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return {"success": True, "dashboard": record}


@router.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, request: Request):
    wsid = current_workspace_id(request)
    if not await persistence.delete_dashboard(dashboard_id, wsid):
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return {"success": True, "id": dashboard_id}


# ── Render: execute every tile ──────────────────────────────────────

async def _run_tile(tile: Dict[str, Any], saved_queries: List[Dict[str, Any]], request: Request) -> Dict[str, Any]:
    sq = next((q for q in saved_queries if q["id"] == tile["saved_query_id"]), None)
    if sq is None:
        return {
            "tile_id": tile["id"],
            "saved_query_id": tile["saved_query_id"],
            "title": tile.get("title"),
            "chart_type": tile.get("chart_type", "table"),
            "status": "missing",
            "error": "Referenced saved query no longer exists",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": 0,
        }

    from shared.data_utils import build_schema_context_cached
    from shared.duckdb_factory import new_connection

    tenant = _request_tenant(request)
    con = new_connection()
    started = time.perf_counter()
    try:
        await build_schema_context_cached(con, tenant, use_llm=False)

        def _run() -> tuple[list[str], list[tuple]]:
            cur = con.execute(sq["sql"])
            return [d[0] for d in cur.description], cur.fetchall()

        columns, rows = await asyncio.to_thread(_run)
        elapsed = (time.perf_counter() - started) * 1000
        # Cap preview to 500 rows so dashboards don't push megabytes per tile
        preview_rows = rows[:500]
        return {
            "tile_id": tile["id"],
            "saved_query_id": tile["saved_query_id"],
            "title": tile.get("title") or sq.get("name"),
            "chart_type": tile.get("chart_type", "table"),
            "status": "success",
            "columns": columns,
            "rows": [list(r) for r in preview_rows],
            "row_count": len(rows),
            "execution_time_ms": round(elapsed, 1),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "tile_id": tile["id"],
            "saved_query_id": tile["saved_query_id"],
            "title": tile.get("title") or sq.get("name"),
            "chart_type": tile.get("chart_type", "table"),
            "status": "error",
            "error": sanitize_error(exc, logger=logger, context="dashboard tile run"),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


@router.post("/dashboards/{dashboard_id}/render")
async def render_dashboard(dashboard_id: str, request: Request):
    """Execute every tile's underlying saved query and return rows."""
    # Sprint P-1: saved queries are now in the gateway persistence
    # layer (workspace-indexed SQL), not an in-process list.
    wsid = current_workspace_id(request)
    record = await persistence.get_dashboard(dashboard_id, wsid)
    if record is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Workspace filtering happens at the SQL level via the composite
    # index — no more O(n) Python filter.
    saved_queries = await persistence.list_saved_queries(wsid)

    tile_results = await asyncio.gather(
        *[_run_tile(t, saved_queries, request) for t in record.get("tiles", [])],
        return_exceptions=False,
    )
    return {
        "success": True,
        "dashboard_id": dashboard_id,
        "rendered_at": datetime.now().isoformat(),
        "tiles": tile_results,
    }
