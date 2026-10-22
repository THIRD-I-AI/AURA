"""Audit round 8, workspaces area.

BUG-494: the workspace registry was a list in one process's memory. Under the prod
``--workers 4`` gateway a workspace created on one worker was invisible to the other
three (list missing it, PATCH/DELETE 404), a restart wiped every workspace, and ids
were a millisecond timestamp, so two creates in the same millisecond collided.

A second worker is simulated by reloading the router module: a fresh process holds
nothing the first one kept in memory.
"""
from __future__ import annotations

import importlib
import uuid
from datetime import datetime

import pytest
from starlette.requests import Request


def _request(org_id=None, headers=None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})
    if org_id is not None:
        req.state.user = {"org_id": org_id}
    return req


@pytest.mark.asyncio
async def test_workspace_created_on_one_worker_is_seen_by_another():
    from api_gateway.routers import workspaces

    org = f"org-{uuid.uuid4().hex[:8]}"
    created = await workspaces.create_workspace(workspaces.WorkspaceCreate(name="Finance"), _request(org))
    ws_id = created["workspace"]["id"]

    other_worker = importlib.reload(workspaces)
    listed = await other_worker.list_workspaces(_request(org))
    assert ws_id in [w["id"] for w in listed["workspaces"]]

    renamed = await other_worker.update_workspace(
        ws_id, other_worker.WorkspaceUpdate(name="Finance EMEA"), _request(org))
    assert renamed["workspace"]["name"] == "Finance EMEA"

    third_worker = importlib.reload(workspaces)
    listed = await third_worker.list_workspaces(_request(org))
    assert {w["id"]: w["name"] for w in listed["workspaces"]}[ws_id] == "Finance EMEA"

    assert (await third_worker.delete_workspace(ws_id, _request(org)))["success"] is True
    listed = await importlib.reload(workspaces).list_workspaces(_request(org))
    assert ws_id not in [w["id"] for w in listed["workspaces"]]


@pytest.mark.asyncio
async def test_tenant_isolation_holds_across_workers():
    from fastapi import HTTPException

    from api_gateway.routers import workspaces

    org_a, org_b = (f"org-{uuid.uuid4().hex[:8]}" for _ in range(2))
    ws_id = (await workspaces.create_workspace(
        workspaces.WorkspaceCreate(name="A only"), _request(org_a)))["workspace"]["id"]

    other = importlib.reload(workspaces)
    assert ws_id not in [w["id"] for w in (await other.list_workspaces(_request(org_b)))["workspaces"]]
    for call in (
        other.update_workspace(ws_id, other.WorkspaceUpdate(name="x"), _request(org_b)),
        other.delete_workspace(ws_id, _request(org_b)),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_two_creates_in_the_same_millisecond_get_distinct_ids(monkeypatch):
    from api_gateway.routers import workspaces

    frozen = datetime(2026, 10, 21, 12, 0, 0, 123000)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(workspaces, "datetime", _FrozenDatetime)
    org = f"org-{uuid.uuid4().hex[:8]}"
    a = (await workspaces.create_workspace(workspaces.WorkspaceCreate(name="a"), _request(org)))["workspace"]
    b = (await workspaces.create_workspace(workspaces.WorkspaceCreate(name="b"), _request(org)))["workspace"]
    assert a["id"] != b["id"]


@pytest.mark.asyncio
async def test_unauthenticated_scope_is_not_rerouted_to_default_by_another_worker():
    from api_gateway.routers import workspaces

    ws_id = (await workspaces.create_workspace(
        workspaces.WorkspaceCreate(name="Open-mode folder"), _request()))["workspace"]["id"]

    other = importlib.reload(workspaces)
    assert other.current_workspace_id(_request(headers={other._HEADER: ws_id})) == ws_id
    # A header that is not a workspace id at all still falls back to default.
    assert other.current_workspace_id(_request(headers={other._HEADER: "ghost"})) == other.DEFAULT_WORKSPACE_ID
