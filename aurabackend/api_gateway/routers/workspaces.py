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

import os
import re
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

def _request_tenant(request: Request) -> Optional[str]:
    """The caller's tenant (org) id from the verified principal that
    ``JWTAuthMiddleware`` stashes on ``request.state.user`` — or ``None``
    when the request is unauthenticated (dev/open mode)."""
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        return None
    tenant = user.get("org_id") or user.get("sub")
    return str(tenant) if tenant else None


def current_workspace_id(request: Request) -> str:
    """The effective data-isolation key for the request.

    SECURITY (Phase 1B — tenant isolation): when the request carries a
    verified identity, the isolation boundary is the caller's TENANT
    (``org_id`` from the token), **not** the client ``X-Workspace-Id``
    header. A token holder must not be able to read another org's data by
    naming its workspace. A within-tenant folder selected via the header is
    namespaced *under* the tenant (``<tenant>::<folder>``) so it can never
    escape the org; the default folder is the tenant itself.

    Unauthenticated requests (dev/open mode, no JWT middleware) keep the
    legacy header-scoped behaviour and never raise — an unknown id falls
    back to ``default``.
    """
    raw = (request.headers.get(_HEADER) or "").strip()
    tenant = _request_tenant(request)
    if tenant is None:
        wsid = raw or DEFAULT_WORKSPACE_ID
        with _workspaces_lock:
            known = {w["id"] for w in _workspaces_store}
        return wsid if wsid in known else DEFAULT_WORKSPACE_ID
    if not raw or raw == DEFAULT_WORKSPACE_ID:
        return tenant
    return f"{tenant}::{raw}"


def workspace_exists(wsid: str) -> bool:
    with _workspaces_lock:
        return any(w["id"] == wsid for w in _workspaces_store)


# ── Tenant upload-directory helpers ─────────────────────────────────

_TENANT_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")


def tenant_dir_name(tenant) -> str:
    """Filesystem-safe slug for a tenant id.

    Strips anything outside [A-Za-z0-9_-] so a hostile org_id cannot
    traverse the upload hierarchy; empty or None -> 'default'.
    """
    slug = _TENANT_SLUG_RE.sub("", str(tenant or "")).strip("-_")
    return slug or "default"


def _tenant_upload_dir_for(uploads_root: str, tenant) -> str:
    """Resolve <uploads_root>/<slug>, asserting path containment.

    Pure (no request object) so it is fully unit-testable; the
    request-bound wrapper is tenant_upload_dir().
    """
    name = tenant_dir_name(tenant)
    path = os.path.join(uploads_root, name)
    if os.path.commonpath((os.path.abspath(path), os.path.abspath(uploads_root))) != os.path.abspath(uploads_root):
        path = os.path.join(uploads_root, "default")
    os.makedirs(path, exist_ok=True)
    return path


_UPLOADS_ROOT = os.getenv("AURA_UPLOADS_ROOT") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "uploads",
)


def tenant_upload_dir(request) -> str:
    """Return the caller's per-tenant upload directory (request-bound).

    Delegates to _tenant_upload_dir_for with the request's tenant id
    (from the verified JWT via _request_tenant). Unauthenticated
    requests fall back to the shared 'default' bucket.
    """
    return _tenant_upload_dir_for(_UPLOADS_ROOT, _request_tenant(request))


def default_upload_dir() -> str:
    """The shared 'default' bucket for paths with no request/tenant
    (e.g. the background scheduler). Mirrors pre-S42 behavior."""
    return _tenant_upload_dir_for(_UPLOADS_ROOT, None)


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
