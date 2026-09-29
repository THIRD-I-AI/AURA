"""BUG-226: scheduler_service has no tenant model, so the gateway's /scheduler/* proxies
must be admin-only; /scheduler/admin/cleanup must not accept retention_days < 1."""
import pytest
from fastapi.testclient import TestClient

from shared.auth import create_access_token

V1 = "/api/v1"
ROUTES = [
    ("get", "/scheduler/jobs"), ("post", "/scheduler/jobs"), ("get", "/scheduler/jobs/j1"),
    ("put", "/scheduler/jobs/j1"), ("delete", "/scheduler/jobs/j1"),
    ("post", "/scheduler/jobs/j1/pause"), ("post", "/scheduler/jobs/j1/resume"),
    ("post", "/scheduler/jobs/j1/execute"), ("post", "/scheduler/jobs/j1/run"),
    ("get", "/scheduler/executions"), ("get", "/scheduler/executions/e1"),
    ("get", "/scheduler/executions/e1/logs"), ("post", "/scheduler/admin/cleanup"),
]


@pytest.fixture()
def client(monkeypatch):
    from api_gateway.main import app
    from api_gateway.routers import pipelines

    calls = []

    async def fake_scheduler(method, path, timeout, request=None, **kw):
        calls.append((method, path, kw.get("params")))
        return {"proxied": path}

    monkeypatch.setattr(pipelines, "_scheduler", fake_scheduler)
    c = TestClient(app)
    c.calls = calls
    return c


def _hdr(role):
    return {"Authorization": f"Bearer {create_access_token({'sub': 'u-' + role, 'org_id': 'o', 'role': role})}"}


def _call(client, verb, path, headers):
    kw = {"headers": headers}
    if verb in ("post", "put"):
        kw["json"] = {}
    return getattr(client, verb)(V1 + path, **kw)


@pytest.mark.parametrize("verb,path", ROUTES)
def test_non_admin_user_is_refused_and_nothing_is_proxied(client, verb, path):
    resp = _call(client, verb, path, _hdr("user"))
    assert resp.status_code == 403, (path, resp.status_code, resp.text)
    assert client.calls == []


@pytest.mark.parametrize("verb,path", ROUTES)
def test_unauthenticated_caller_is_refused(client, verb, path):
    resp = _call(client, verb, path, {})
    assert resp.status_code == 401, (path, resp.status_code, resp.text)
    assert client.calls == []


@pytest.mark.parametrize("verb,path", ROUTES)
def test_admin_is_proxied(client, verb, path):
    resp = _call(client, verb, path, _hdr("admin"))
    assert resp.status_code == 200, (path, resp.status_code, resp.text)
    assert len(client.calls) == 1


@pytest.mark.parametrize("days", [0, -5])
def test_cleanup_rejects_retention_below_one_day(client, days):
    resp = client.post(f"{V1}/scheduler/admin/cleanup?retention_days={days}", headers=_hdr("admin"))
    assert resp.status_code == 422
    assert client.calls == []
