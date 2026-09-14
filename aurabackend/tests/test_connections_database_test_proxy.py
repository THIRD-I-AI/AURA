"""
BUG-060 -- GET /databases/test/{db_type} (api_gateway/routers/connections.py)
relayed the downstream database-service's JSON body as an HTTP 200 regardless
of the upstream's actual status code, and never forwarded the caller's
Authorization header -- violating backend.md's explicit proxy rule ("forward
the caller's Authorization header and preserve the upstream status code").
Same class of bug as the UASR/causal_service proxies, fixed the same way.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

V1 = "/api/v1"


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    from api_gateway.main import app
    return TestClient(app)


def test_forwards_authorization_header(gw, monkeypatch):
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

        async def get(self, url, headers=None, **kwargs):
            captured["headers"] = headers or {}
            return _FakeResponse()

    from api_gateway.routers import connections
    monkeypatch.setattr(connections.httpx, "AsyncClient", _FakeClient)

    r = gw.get(f"{V1}/databases/test/postgresql", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200, r.text
    assert captured["headers"].get("Authorization") == "Bearer test-token"


def test_preserves_upstream_error_status_code(gw, monkeypatch):
    """An upstream 401 must reach the caller as a 401, not be flattened to
    200 -- the exact bug: `return response.json()` discarded status_code."""

    class _FakeResponse:
        status_code = 401

        def json(self):
            return {"detail": "Bearer token required"}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kwargs):
            return _FakeResponse()

    from api_gateway.routers import connections
    monkeypatch.setattr(connections.httpx, "AsyncClient", _FakeClient)

    r = gw.get(f"{V1}/databases/test/postgresql")
    assert r.status_code == 401, r.text
    assert r.json() == {"detail": "Bearer token required"}


def test_unreachable_service_returns_503_not_200(gw, monkeypatch):
    monkeypatch.setenv("DATABASE_SERVICE_URL", "http://localhost:1")

    r = gw.get(f"{V1}/databases/test/postgresql")
    assert r.status_code == 503, r.text
