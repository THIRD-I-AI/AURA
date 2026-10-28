"""Audit round 10, gateway area: BUG-539, BUG-540, BUG-541."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ORIGIN = "http://localhost:5173"


@pytest.fixture()
def make_service(monkeypatch):
    """create_service with auth off, CORS for ORIGIN and tiny body limits."""
    from shared import service_factory

    monkeypatch.setattr(service_factory.settings, "jwt_enabled", False)
    monkeypatch.setattr(service_factory.settings, "api_key", None)
    monkeypatch.setattr(service_factory.settings, "rate_limit_enabled", False)
    monkeypatch.setattr(service_factory.settings, "cors_origins", [ORIGIN])
    monkeypatch.setenv("AURA_MAX_REQUEST_BODY_BYTES", "1000")
    monkeypatch.setenv("AURA_MAX_UPLOAD_BODY_BYTES", "4000")

    def _make(**kwargs):
        return service_factory.create_service(name="T", service_tag="t", version="1", **kwargs)

    return _make


# ── BUG-539: the '/upload' exemption was a suffix match on a caller-controlled path ──

def _body_app(make_service):
    app = make_service()

    @app.patch("/api/v1/hooks/{hook_id}")
    async def update_hook(hook_id: str, request: Request):
        return {"read": len(await request.body())}

    @app.post("/api/v1/things/{thing_id}")
    async def post_thing(thing_id: str, request: Request):
        return {"read": len(await request.body())}

    @app.post("/api/v1/upload")
    async def upload(request: Request):
        return {"read": len(await request.body())}

    return TestClient(app)


def test_bug539_patch_to_a_path_ending_in_upload_is_still_limited(make_service):
    client = _body_app(make_service)
    assert client.patch("/api/v1/hooks/abc", content=b"x" * 50_000).status_code == 413
    # hook_id='upload' used to make the path end in '/upload' and skip every limit.
    r = client.patch("/api/v1/hooks/upload", content=b"x" * 50_000)
    assert r.status_code == 413, r.text


def test_bug539_chunked_post_to_a_path_ending_in_upload_is_cut_off(make_service):
    client = _body_app(make_service)

    def chunks():
        for _ in range(100):
            yield b"x" * 500

    r = client.post("/api/v1/things/upload", content=chunks())
    assert r.status_code == 413, r.text


def test_bug539_the_real_upload_route_keeps_its_larger_limit(make_service):
    client = _body_app(make_service)
    # Over the 1000-byte JSON limit, under the 4000-byte upload limit.
    assert client.post("/api/v1/upload", content=b"x" * 2500).json() == {"read": 2500}
    assert client.post("/api/v1/upload", content=b"x" * 5000).status_code == 413


# ── BUG-540: public /health echoed raw probe exception text ──

SECRET = 'password authentication failed for user "aura" at 10.0.3.7:5432'


def test_bug540_health_does_not_leak_probe_error_text(make_service):
    async def db_probe():
        return f"db unreachable: OperationalError: {SECRET}"

    async def raising_probe():
        raise OSError(f"[Errno 30] Read-only file system: '/data/secret-layout' {SECRET}")

    async def ok_probe():
        return None

    app = make_service(health_checks={"db": db_probe, "data_dir": raising_probe, "fine": ok_probe})
    r = TestClient(app).get("/health")
    assert r.status_code == 503
    body = r.text
    assert SECRET not in body and "/data/secret-layout" not in body and "OSError" not in body
    checks = r.json()["checks"]
    assert checks["fine"] == "ok"
    assert checks["db"] != "ok" and checks["data_dir"] != "ok"


# ── BUG-541: unhandled-exception 500s skipped CORS and X-Request-ID ──

def _crashing_app(make_service):
    app = make_service()

    @app.get("/api/v1/boom")
    async def boom():
        raise RuntimeError("kaboom")

    return TestClient(app, raise_server_exceptions=False)


def test_bug541_cross_origin_500_carries_cors_and_request_id(make_service):
    client = _crashing_app(make_service)
    r = client.get("/api/v1/boom", headers={"Origin": ORIGIN, "X-Request-ID": "req-541"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == ORIGIN
    assert r.headers.get("access-control-allow-credentials") == "true"
    assert r.headers.get("x-request-id") == "req-541"
    assert r.json()["request_id"] == "req-541"
    assert "kaboom" not in r.text


def test_bug541_disallowed_origin_still_gets_no_allow_origin(make_service):
    client = _crashing_app(make_service)
    r = client.get("/api/v1/boom", headers={"Origin": "https://evil.example"})
    assert r.status_code == 500
    assert "access-control-allow-origin" not in r.headers
