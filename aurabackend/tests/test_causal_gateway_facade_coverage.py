"""
Regression coverage for the gateway's causal_service proxy facade.

causal_service (aurabackend/causal_service/ — DoWhy-GCM root-cause
attribution, falls back to partial-correlation when dowhy isn't installed)
existed on disk, tested, and referenced in the Helm chart, but was not
reachable through the gateway or any docker-compose file — the same class of
gap test_uasr_gateway_facade_coverage.py guards for UASR. This file is that
guard for causal_service's two routes (POST /causal/discover, GET
/causal/info), proxied via api_gateway/routers/pipelines.py's `_causal`
helper (same shape as `_uasr`).
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

V1 = "/api/v1"

_EXCLUDED_CAUSAL_PATHS = {
    "/health", "/metrics", "/docs", "/redoc", "/openapi.json",
}


def _causal_service_routes():
    from causal_service.main import app as causal_app
    out = []
    for route in causal_app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _EXCLUDED_CAUSAL_PATHS:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            out.append((method, route.path))
    return out


def _gateway_route_set():
    from api_gateway.main import app as gw_app
    out = set()
    for route in gw_app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            out.add((method, route.path))
    return out


def test_every_causal_route_has_a_gateway_facade():
    """causal_service path segments map 1:1 onto the gateway (unlike
    counterfactual_service, which strips a prefix) -- /causal/discover on
    the service is /api/v1/causal/discover on the gateway, same as UASR."""
    gateway_routes = _gateway_route_set()
    missing = [
        (method, path, f"{V1}{path}")
        for method, path in _causal_service_routes()
        if (method, f"{V1}{path}") not in gateway_routes
    ]
    assert not missing, f"causal_service routes with no gateway facade: {missing}"


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    from api_gateway.main import app
    return TestClient(app)


def test_causal_discover_returns_503_when_service_unreachable(gw, monkeypatch):
    """causal_service is a separate, optionally-deployed container -- an
    unreachable upstream must read as 503 (a known-absent dependency), not a
    generic 500 that looks like a crash inside the gateway itself. Mirrors
    the UASR proxy's ConnectError handling in pipelines.py's `_uasr`."""
    from api_gateway.routers import pipelines
    monkeypatch.setattr(pipelines, "_CAUSAL_URL", "http://localhost:1")

    r = gw.post(
        f"{V1}/causal/discover",
        json={
            "target_metric": "y",
            "training_data": {"rows": [{"y": 1, "x": 2}]},
            "anomaly_data": {"rows": [{"y": 5, "x": 9}]},
        },
    )
    assert r.status_code == 503, r.text


def test_causal_info_returns_503_when_service_unreachable(gw, monkeypatch):
    from api_gateway.routers import pipelines
    monkeypatch.setattr(pipelines, "_CAUSAL_URL", "http://localhost:1")

    r = gw.get(f"{V1}/causal/info")
    assert r.status_code == 503, r.text


def test_causal_discover_forwards_authorization_header(gw, monkeypatch):
    """The proxy must forward the caller's bearer token upstream -- dropping
    it silently turned an upstream 401 into a browser-visible 200 for UASR
    before that bug was fixed (see backend.md), and `_causal` follows the
    same pattern so it carries the same risk if the header forward is ever
    dropped."""
    captured = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, **kwargs):
            captured["headers"] = headers or {}
            return _FakeResponse()

    from api_gateway.routers import pipelines
    monkeypatch.setattr(pipelines.httpx, "AsyncClient", _FakeClient)

    r = gw.get(f"{V1}/causal/info", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200, r.text
    assert captured["headers"].get("Authorization") == "Bearer test-token"
