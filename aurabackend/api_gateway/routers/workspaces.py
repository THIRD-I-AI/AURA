"""
Workspaces Router
=================
Lightweight tenancy for saved queries, dashboards, and lineage.

Workspaces live in an in-memory store (same pattern as saved queries).
Scoping is header-driven — callers send ``X-Workspace-Id``; when absent
we fall back to the seeded ``default`` workspace. This keeps the API
usable without auth while letting the frontend switch context cleanly.

Exposes ``current_workspace_id()`` so sibling routers can scope their
own stores without duplicating the header plumbing.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from shared.logging_config import get_logger

logger = get_logger("aura.api_gateway.workspaces")

router = APIRouter(tags=["Workspaces"])


# ── Constants & store ───────────────────────────────────────────────

DEFAULT_WORKSPACE_ID = "default"
_HEADER = "X-Workspace-Id"

_workspaces_lock = threading.Lock()
_workspaces_store: List[Dict[str, Any]] = []


def _seed_default() -> None:
    with _workspaces_lock:
        if any(w["id"] == DEFAULT_WORKSPACE_ID for w in _workspaces_store):
            return
        ts = datetime.now().isoformat()
        _workspaces_store.append({
            "id": DEFAULT_WORKSPACE_ID,
            "name": "Default",
            "description": "Default workspace (auto-created)",
            "created_at": ts,
            "updated_at": ts,
        })


_seed_default()


# ── Public helpers — used by other routers ──────────────────────────

def current_workspace_id(request: Request) -> str:
    """Extract the current workspace id from the request header.

    Never raises — unknown ids silently fall back to ``default`` so a
    stale client doesn't brick the app. Callers that want strict mode
    should check ``workspace_exists()`` themselves.
    """
    raw = request.headers.get(_HEADER) or ""
    wsid = raw.strip() or DEFAULT_WORKSPACE_ID
    with _workspaces_lock:
        known = {w["id"] for w in _workspaces_store}
    return wsid if wsid in known else DEFAULT_WORKSPACE_ID


def workspace_exists(wsid: str) -> bool:
    with _workspaces_lock:
        return any(w["id"] == wsid for w in _workspaces_store)


# ── Models ──────────────────────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


# ── CRUD endpoints ──────────────────────────────────────────────────

@router.get("/workspaces")
async def list_workspaces():
    with _workspaces_lock:
        records = list(_workspaces_store)
    records.sort(key=lambda r: (0 if r["id"] == DEFAULT_WORKSPACE_ID else 1, r["name"].lower()))
    return {"success": True, "workspaces": records, "total": len(records)}


@router.post("/workspaces")
async def create_workspace(payload: WorkspaceCreate):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    ts = datetime.now()
    record = {
        "id": f"ws_{int(ts.timestamp() * 1000)}",
        "name": name,
        "description": (payload.description or "").strip() or None,
        "created_at": ts.isoformat(),
        "updated_at": ts.isoformat(),
    }
    with _workspaces_lock:
        _workspaces_store.append(record)
    return {"success": True, "workspace": record}


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(workspace_id: str, payload: WorkspaceUpdate):
    with _workspaces_lock:
        record = next((w for w in _workspaces_store if w["id"] == workspace_id), None)
        if record is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if payload.name is not None:
            new_name = payload.name.strip()
            if not new_name:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            record["name"] = new_name
        if payload.description is not None:
            record["description"] = payload.description.strip() or None
        record["updated_at"] = datetime.now().isoformat()
    return {"success": True, "workspace": record}


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str):
    if workspace_id == DEFAULT_WORKSPACE_ID:
        raise HTTPException(status_code=400, detail="Cannot delete the default workspace")
    with _workspaces_lock:
        before = len(_workspaces_store)
        _workspaces_store[:] = [w for w in _workspaces_store if w["id"] != workspace_id]
        if len(_workspaces_store) == before:
            raise HTTPException(status_code=404, detail="Workspace not found")
    return {"success": True, "id": workspace_id}
