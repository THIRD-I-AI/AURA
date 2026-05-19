"""
Dashboards Router
=================
First-class dashboard objects composed of tiles that reference saved
queries. Tiles render by executing the referenced saved query's SQL
against the uploaded-file DuckDB (same path the saved-query scheduler
uses).

Storage is in-memory — mirrors the saved-queries library. No drag/drop
layout yet; tiles render in order.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.routers.workspaces import DEFAULT_WORKSPACE_ID, current_workspace_id
from shared.logging_config import get_logger

logger = get_logger("aura.api_gateway.dashboards")

router = APIRouter(tags=["Dashboards"])


# ── In-memory store ────────────────────────────────────────────────

_dashboards_lock = threading.Lock()
_dashboards_store: List[Dict[str, Any]] = []  # newest first
_MAX_DASHBOARDS = 200


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


def _in_workspace(record: Dict[str, Any], wsid: str) -> bool:
    return (record.get("workspace_id") or DEFAULT_WORKSPACE_ID) == wsid


# ── CRUD endpoints ──────────────────────────────────────────────────

@router.get("/dashboards")
async def list_dashboards(request: Request):
    """Return dashboards for the caller's workspace, newest-first."""
    wsid = current_workspace_id(request)
    with _dashboards_lock:
        records = [r for r in _dashboards_store if _in_workspace(r, wsid)]
    return {"success": True, "dashboards": records, "total": len(records)}


@router.post("/dashboards")
async def create_dashboard(payload: DashboardCreate, request: Request):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    wsid = current_workspace_id(request)
    ts = datetime.now()
    tiles = [_tile_to_record(t, i) for i, t in enumerate(payload.tiles)]
    record = {
        "id": f"dash_{int(ts.timestamp() * 1000)}",
        "workspace_id": wsid,
        "name": name,
        "description": (payload.description or "").strip() or None,
        "tiles": tiles,
        "created_at": ts.isoformat(),
        "updated_at": ts.isoformat(),
    }
    with _dashboards_lock:
        _dashboards_store.insert(0, record)
        if len(_dashboards_store) > _MAX_DASHBOARDS:
            del _dashboards_store[_MAX_DASHBOARDS:]
    return {"success": True, "dashboard": record}


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str, request: Request):
    wsid = current_workspace_id(request)
    with _dashboards_lock:
        record = next(
            (r for r in _dashboards_store if r["id"] == dashboard_id and _in_workspace(r, wsid)),
            None,
        )
    if record is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return {"success": True, "dashboard": record}


@router.patch("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: str, payload: DashboardUpdate, request: Request):
    wsid = current_workspace_id(request)
    with _dashboards_lock:
        record = next(
            (r for r in _dashboards_store if r["id"] == dashboard_id and _in_workspace(r, wsid)),
            None,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        if payload.name is not None:
            new_name = payload.name.strip()
            if not new_name:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            record["name"] = new_name
        if payload.description is not None:
            record["description"] = payload.description.strip() or None
        if payload.tiles is not None:
            record["tiles"] = [_tile_to_record(t, i) for i, t in enumerate(payload.tiles)]
        record["updated_at"] = datetime.now().isoformat()
    return {"success": True, "dashboard": record}


@router.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, request: Request):
    wsid = current_workspace_id(request)
    with _dashboards_lock:
        before = len(_dashboards_store)
        _dashboards_store[:] = [
            r for r in _dashboards_store
            if not (r["id"] == dashboard_id and _in_workspace(r, wsid))
        ]
        if len(_dashboards_store) == before:
            raise HTTPException(status_code=404, detail="Dashboard not found")
    return {"success": True, "id": dashboard_id}


# ── Render: execute every tile ──────────────────────────────────────

async def _run_tile(tile: Dict[str, Any], saved_queries: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    import pathlib

    import duckdb

    from shared.data_utils import build_schema_context_cached

    base = pathlib.Path(__file__).resolve().parent.parent.parent
    upload_dirs = [
        base / "data" / "uploads",
        base / "api_gateway" / "uploads",
        base.parent / "uploads",
    ]

    con = duckdb.connect(":memory:")
    started = time.time()
    try:
        await build_schema_context_cached(con, upload_dirs, use_llm=False)

        def _run() -> tuple[list[str], list[tuple]]:
            cur = con.execute(sq["sql"])
            return [d[0] for d in cur.description], cur.fetchall()

        columns, rows = await asyncio.to_thread(_run)
        elapsed = (time.time() - started) * 1000
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
            "error": str(exc),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": round((time.time() - started) * 1000, 1),
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
    from api_gateway import persistence

    wsid = current_workspace_id(request)
    with _dashboards_lock:
        record = next(
            (r for r in _dashboards_store if r["id"] == dashboard_id and _in_workspace(r, wsid)),
            None,
        )
    if record is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Workspace filtering happens at the SQL level via the composite
    # index — no more O(n) Python filter.
    saved_queries = await persistence.list_saved_queries(wsid)

    tile_results = await asyncio.gather(
        *[_run_tile(t, saved_queries) for t in record.get("tiles", [])],
        return_exceptions=False,
    )
    return {
        "success": True,
        "dashboard_id": dashboard_id,
        "rendered_at": datetime.now().isoformat(),
        "tiles": tile_results,
    }
