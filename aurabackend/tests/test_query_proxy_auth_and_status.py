"""BUG-236: the execution-sandbox and orchestration proxies dropped the caller's
Authorization header (so a JWT-enforcing upstream 401'd every call) and collapsed the
upstream status into HTTP 200 with an error body."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.routers import queries


def _fake_upstream(monkeypatch, status: int, body: dict):
    seen = {"headers": None, "urls": []}

    class _FakeClient:
        def __init__(self, *a, headers=None, **k):
            seen["headers"] = dict(headers or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            seen["urls"].append(url)
            return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return seen


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(queries, "current_workspace_id", lambda request: "ws-test")
    app = FastAPI()
    app.include_router(queries.router)
    return TestClient(app)


EXECUTE = {"sql": "SELECT 1", "connection_id": "c1"}
GENERATE = {"session_id": "s1", "prompt": "total sales"}


def test_execute_forwards_the_callers_authorization_to_the_sandbox(client, monkeypatch):
    seen = _fake_upstream(monkeypatch, 200, {"columns": ["x"], "rows": [[1]]})
    r = client.post("/execute", json=EXECUTE, headers={"Authorization": "Bearer caller-token"})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == [{"x": 1}]
    assert seen["headers"].get("Authorization") == "Bearer caller-token"


def test_execute_passes_an_upstream_401_through_instead_of_http_200(client, monkeypatch):
    _fake_upstream(monkeypatch, 401, {"detail": "Token has expired"})
    r = client.post("/execute", json=EXECUTE, headers={"Authorization": "Bearer stale"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Token has expired"


def test_execute_maps_an_upstream_5xx_to_502_without_proxying_its_body(client, monkeypatch):
    _fake_upstream(monkeypatch, 500, {"detail": "psycopg2.OperationalError: FATAL password for user aura"})
    r = client.post("/execute", json=EXECUTE)
    assert r.status_code == 502
    assert "psycopg2" not in r.text and "password" not in r.text


def test_generate_query_forwards_authorization_and_keeps_the_status(client, monkeypatch):
    seen = _fake_upstream(monkeypatch, 200, {"status": "Success", "final_query": "SELECT 1"})
    r = client.post("/generate_query", json=GENERATE, headers={"Authorization": "Bearer caller-token"})
    assert r.status_code == 200, r.text
    assert seen["headers"].get("Authorization") == "Bearer caller-token"

    _fake_upstream(monkeypatch, 401, {"detail": "Unauthorized"})
    assert client.post("/generate_query", json=GENERATE).status_code == 401

    _fake_upstream(monkeypatch, 503, {"detail": "down"})
    assert client.post("/generate_query", json=GENERATE).status_code == 502
