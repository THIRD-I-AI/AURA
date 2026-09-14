"""
BUG-057 -- /workspaces CRUD (list/create/update/delete) had no tenant
scoping at all: every authenticated caller from any tenant shared one
global `_workspaces_store` registry. Any tenant could list every other
tenant's workspace names/descriptions, and (ids being sequential-ish
`ws_<timestamp_ms>`, easily brute-forced) rename or delete another
tenant's workspace record.

Fixed: `tenant_id` is stamped on every record at creation and enforced
on list/update/delete. The seeded DEFAULT_WORKSPACE_ID stays globally
visible/protected-from-delete for every caller -- it's a shared system
placeholder, not tenant data.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from starlette.requests import Request  # noqa: E402

from api_gateway.routers.workspaces import (  # noqa: E402
    DEFAULT_WORKSPACE_ID,
    WorkspaceCreate,
    WorkspaceUpdate,
    _workspaces_store,
    create_workspace,
    delete_workspace,
    list_workspaces,
    update_workspace,
)


def _request(org_id: str) -> Request:
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    req.state.user = {"org_id": org_id}
    return req


@pytest.fixture(autouse=True)
def _clean_store():
    """Isolate each test: snapshot/restore the module-level store."""
    snapshot = list(_workspaces_store)
    yield
    _workspaces_store[:] = snapshot


@pytest.mark.asyncio
async def test_list_workspaces_only_shows_the_callers_tenant():
    await create_workspace(WorkspaceCreate(name="Tenant A's workspace"), _request("bug057-tenant-a"))
    await create_workspace(WorkspaceCreate(name="Tenant B's workspace"), _request("bug057-tenant-b"))

    result_a = await list_workspaces(_request("bug057-tenant-a"))
    names_a = {w["name"] for w in result_a["workspaces"]}
    assert "Tenant A's workspace" in names_a
    assert "Tenant B's workspace" not in names_a

    result_b = await list_workspaces(_request("bug057-tenant-b"))
    names_b = {w["name"] for w in result_b["workspaces"]}
    assert "Tenant B's workspace" in names_b
    assert "Tenant A's workspace" not in names_b


@pytest.mark.asyncio
async def test_default_workspace_visible_to_every_tenant():
    result = await list_workspaces(_request("bug057-any-tenant"))
    assert any(w["id"] == DEFAULT_WORKSPACE_ID for w in result["workspaces"])


@pytest.mark.asyncio
async def test_cannot_rename_another_tenants_workspace_even_knowing_its_id():
    created = await create_workspace(WorkspaceCreate(name="Secret Project"), _request("bug057-tenant-a"))
    ws_id = created["workspace"]["id"]

    with pytest.raises(Exception) as exc_info:
        await update_workspace(ws_id, WorkspaceUpdate(name="Pwned"), _request("bug057-tenant-b"))
    assert getattr(exc_info.value, "status_code", None) == 404

    # Confirm it genuinely wasn't renamed.
    result = await list_workspaces(_request("bug057-tenant-a"))
    names = {w["name"] for w in result["workspaces"]}
    assert "Secret Project" in names
    assert "Pwned" not in names


@pytest.mark.asyncio
async def test_cannot_delete_another_tenants_workspace_even_knowing_its_id():
    created = await create_workspace(WorkspaceCreate(name="Do Not Delete Me"), _request("bug057-tenant-a"))
    ws_id = created["workspace"]["id"]

    with pytest.raises(Exception) as exc_info:
        await delete_workspace(ws_id, _request("bug057-tenant-b"))
    assert getattr(exc_info.value, "status_code", None) == 404

    result = await list_workspaces(_request("bug057-tenant-a"))
    assert any(w["id"] == ws_id for w in result["workspaces"]), "the real owner's workspace must survive"


@pytest.mark.asyncio
async def test_owner_can_update_and_delete_their_own_workspace():
    created = await create_workspace(WorkspaceCreate(name="Mine"), _request("bug057-tenant-a"))
    ws_id = created["workspace"]["id"]

    updated = await update_workspace(ws_id, WorkspaceUpdate(name="Mine (renamed)"), _request("bug057-tenant-a"))
    assert updated["workspace"]["name"] == "Mine (renamed)"

    deleted = await delete_workspace(ws_id, _request("bug057-tenant-a"))
    assert deleted["success"] is True
