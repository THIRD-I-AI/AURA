"""BUG-230: a pipeline hook may only target a pipeline in its owner's workspace, and
firing a hook must never run another tenant's pipeline."""
import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from api_gateway import persistence as p

V1 = "/api/v1"


def _pipe(pid, ws):
    return {"id": pid, "workspace_id": ws, "name": pid, "description": "d",
            "definition": {"id": pid, "name": pid, "source": {"type": "file", "file_name": "x.csv"}, "steps": []},
            "status": "draft", "source_label": "file:x.csv", "sink_type": "table",
            "step_count": 0, "tags": [], "created_at": "2026-09-30T00:00:00", "updated_at": None}


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / ('gw_' + uuid.uuid4().hex + '.db')}")
    p._engine = None
    p._session_factory = None
    p._schema_initialized = False
    await p.init_database()
    await p.save_pipeline(_pipe("pipe_victim", "orgB"))
    await p.save_pipeline(_pipe("pipe_own", "orgA"))

    import shared.inbound_hooks as ih
    from api_gateway.routers import inbound_hooks as router_mod

    monkeypatch.setattr(ih, "_STORE_PATH", str(tmp_path / "inbound.json"))
    monkeypatch.setattr(router_mod, "inbound_hooks", ih.InboundHookRegistry())
    ws = {"id": "orgA"}
    monkeypatch.setattr(router_mod, "current_workspace_id", lambda request: ws["id"])

    from api_gateway.main import app
    yield TestClient(app), ws, router_mod
    await p.close_database()


@pytest.mark.asyncio
async def test_cannot_register_a_hook_targeting_another_tenants_pipeline(env):
    client, _, _ = env
    r = client.post(f"{V1}/hooks", json={"slug": "s1", "kind": "pipeline", "target": "pipe_victim"})
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_can_register_a_hook_targeting_own_pipeline(env):
    client, _, _ = env
    r = client.post(f"{V1}/hooks", json={"slug": "s2", "kind": "pipeline", "target": "pipe_own"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_cannot_retarget_an_existing_hook_to_another_tenants_pipeline(env):
    client, _, _ = env
    hook_id = client.post(f"{V1}/hooks", json={"slug": "s3", "kind": "pipeline", "target": "pipe_own"}).json()["hook"]["id"]
    r = client.patch(f"{V1}/hooks/{hook_id}", json={"target": "pipe_victim"})
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_a_pre_existing_cross_tenant_hook_does_not_run_the_victims_pipeline(env, monkeypatch):
    client, _, router_mod = env
    # simulate a hook registered before the create-time check existed
    router_mod.inbound_hooks.register(workspace_id="orgA", slug="legacy", kind="pipeline",
                                      target="pipe_victim", secret=None, description="", pass_payload_as=None)
    ran = []
    from pipeline import engine as pe

    async def _no_run(self, *a, **k):
        ran.append(True)
        raise AssertionError("victim pipeline must not execute")

    monkeypatch.setattr(pe.PipelineEngine, "execute", _no_run)
    r = client.post(f"{V1}/hooks/fire/legacy", json={})
    assert r.status_code == 404, r.text
    assert ran == []
