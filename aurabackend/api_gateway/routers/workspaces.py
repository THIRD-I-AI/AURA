"""
Workspaces Router
=================
Lightweight tenancy for saved queries, dashboards, and lineage.

Workspace records live in the gateway database (``gateway_workspaces``).
Scoping is header-driven — callers send ``X-Workspace-Id``; when absent
we fall back to the shared ``default`` workspace. This keeps the API
usable without auth while letting the frontend switch context cleanly.

Exposes ``current_workspace_id()`` so sibling routers can scope their
own stores without duplicating the header plumbing.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway import persistence
from shared.logging_config import get_logger

logger = get_logger("aura.api_gateway.workspaces")

router = APIRouter(tags=["Workspaces"])


# ── Constants & store ───────────────────────────────────────────────

DEFAULT_WORKSPACE_ID = "default"
_HEADER = "X-Workspace-Id"

# BUG-494: the registry used to be a list in this process's memory, so under several
# gateway workers a workspace existed only on the worker that created it, and a restart
# deleted every one. Records now live in persistence.WorkspaceRow. The shared default
# workspace is a constant, not a row: every tenant sees it and no one may edit it.
_DEFAULT_CREATED_AT = datetime.now().isoformat()
_DEFAULT_RECORD: Dict[str, Any] = {
    "id": DEFAULT_WORKSPACE_ID,
    "name": "Default",
    "description": "Default workspace (auto-created)",
    "created_at": _DEFAULT_CREATED_AT,
    "updated_at": _DEFAULT_CREATED_AT,
    "tenant_id": None,
}

# Generated ids are ws_<hex>; ids from before BUG-494 are ws_<epoch ms>.
_WORKSPACE_ID_RE = re.compile(r"^ws_[0-9A-Za-z]{1,60}$")


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
    legacy header-scoped behaviour and never raise — a header that is not a
    workspace id falls back to ``default``.
    """
    raw = (request.headers.get(_HEADER) or "").strip()
    tenant = _request_tenant(request)
    if tenant is None:
        # BUG-494: this checked the id against the handling worker's in-memory list,
        # so a workspace made on another worker was silently rerouted to the shared
        # default and its writes landed there. This sync helper cannot query the async
        # DB, and open mode has no tenant boundary to defend, so the id's shape decides.
        return raw if _WORKSPACE_ID_RE.match(raw) else DEFAULT_WORKSPACE_ID
    if not raw or raw == DEFAULT_WORKSPACE_ID:
        return tenant
    return f"{tenant}::{raw}"


def tenant_from_workspace_id(workspace_id: Optional[str]) -> Optional[str]:
    """Best-effort recovery of the storage tenant that owns a workspace id,
    for background/webhook paths (inbound_hooks.py's pipeline trigger) that
    have no Request to read the verified JWT from directly.

    current_workspace_id() builds an authenticated id as
    ``"<tenant>::<folder>"`` (a default folder collapses to the bare
    tenant), so splitting on the first "::" recovers the storage tenant
    exactly for every authenticated workspace — the same tenant
    get_storage_backend().write()/exists() were called with at upload time.

    Residual gap (documented, not fixed here): an UNAUTHENTICATED caller's
    workspace id is the raw client-supplied X-Workspace-Id header value (or
    "default"), which does NOT identify the storage tenant a file was
    uploaded under — an unauthenticated upload always lands in the shared
    "default" storage bucket regardless of that header, because
    _request_tenant() returns None for it. A pipeline saved under a custom
    unauthenticated workspace folder therefore recovers the WRONG storage
    tenant here. This only matters for a deployment running fully open (no
    JWT middleware) AND using non-default X-Workspace-Id folders AND
    triggering pipelines via inbound webhooks — narrow enough that fixing it
    needs its own design pass on workspace_id itself, not a fix folded into
    BUG-035's router-level storage-backend change.
    """
    if not workspace_id:
        return None
    return workspace_id.split("::", 1)[0]


# ── Tenant upload-directory helpers ─────────────────────────────────

def tenant_dir_name(tenant) -> str:
    """Filesystem-safe slug for a tenant id -- delegates to the single definition in
    shared/storage/base.py so the two can never drift (BUG-231)."""
    from shared.storage.base import tenant_slug

    return tenant_slug(tenant)


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
#
# BUG-057: these four endpoints operated on one shared workspace
# registry with no reference to the caller's tenant at all --
# any authenticated caller could list every tenant's workspace records,
# and (ids being sequential-ish `ws_<timestamp_ms>`, easily brute-forced)
# rename or delete another tenant's workspace by id. `tenant_id` is now
# stamped on every record at creation and enforced on read/update/delete.
# The seeded DEFAULT_WORKSPACE_ID keeps `tenant_id=None` and stays
# globally visible/protected-from-delete for every caller (authenticated
# or not) -- it's a shared system placeholder, not tenant data.


@router.get("/workspaces")
async def list_workspaces(request: Request):
    records = await persistence.list_workspaces(_request_tenant(request))
    records.sort(key=lambda r: r["name"].lower())
    records.insert(0, dict(_DEFAULT_RECORD))
    return {"success": True, "workspaces": records, "total": len(records)}


@router.post("/workspaces")
async def create_workspace(payload: WorkspaceCreate, request: Request):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    ts = datetime.now().isoformat()
    record = {
        # BUG-494: a millisecond timestamp collided for two creates in the same ms
        # (e.g. on two workers), and the two records then shared one data namespace.
        "id": f"ws_{secrets.token_hex(8)}",
        "name": name,
        "description": (payload.description or "").strip() or None,
        "created_at": ts,
        "updated_at": ts,
        "tenant_id": _request_tenant(request),
    }
    await persistence.insert_workspace(record)
    return {"success": True, "workspace": record}


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(workspace_id: str, payload: WorkspaceUpdate, request: Request):
    # BUG-245: the default workspace is ONE record shared by every tenant, so an edit by
    # any caller changed its name and description for all of them. Same guard as delete.
    if workspace_id == DEFAULT_WORKSPACE_ID:
        raise HTTPException(status_code=400, detail="Cannot modify the default workspace")
    fields: Dict[str, Any] = {"updated_at": datetime.now().isoformat()}
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        fields["name"] = new_name
    if payload.description is not None:
        fields["description"] = payload.description.strip() or None
    record = await persistence.update_workspace(workspace_id, _request_tenant(request), fields)
    if record is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"success": True, "workspace": record}


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str, request: Request):
    if workspace_id == DEFAULT_WORKSPACE_ID:
        raise HTTPException(status_code=400, detail="Cannot delete the default workspace")
    if not await persistence.delete_workspace(workspace_id, _request_tenant(request)):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"success": True, "id": workspace_id}
