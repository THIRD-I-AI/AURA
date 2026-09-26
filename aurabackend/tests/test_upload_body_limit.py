"""BUG-179: the 25MB upload limit only fired after the whole multipart body was spooled to disk."""
from __future__ import annotations

from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient

from shared.middleware import UploadBodyLimitMiddleware


def _app(max_bytes: int, calls: list) -> FastAPI:
    app = FastAPI()
    app.add_middleware(UploadBodyLimitMiddleware, max_bytes=max_bytes)

    @app.post("/api/v1/upload")
    async def upload(file: UploadFile = File(...)):
        calls.append(file.filename)
        return {"ok": True}

    @app.post("/api/v1/other")
    async def other(payload: dict):
        calls.append("other")
        return {"ok": True}

    return app


def test_oversized_declared_body_is_rejected_before_the_handler_runs():
    calls: list = []
    client = TestClient(_app(1024, calls))
    r = client.post("/api/v1/upload", files={"file": ("big.csv", b"x" * 5000)})
    assert r.status_code == 413
    assert "upload limit" in r.json()["detail"]
    assert calls == [], "the handler (and multipart parsing) must not run for an oversized body"


def test_body_within_the_limit_passes_through():
    calls: list = []
    client = TestClient(_app(1024 * 1024, calls))
    r = client.post("/api/v1/upload", files={"file": ("ok.csv", b"a,b\n1,2\n")})
    assert r.status_code == 200 and calls == ["ok.csv"]


def test_only_the_upload_route_is_limited():
    calls: list = []
    client = TestClient(_app(100, calls))
    r = client.post("/api/v1/other", json={"pad": "y" * 2000})
    assert r.status_code == 200 and calls == ["other"]


def test_the_service_factory_installs_it_with_the_documented_limit():
    import inspect

    from shared import service_factory

    src = inspect.getsource(service_factory)
    assert "UploadBodyLimitMiddleware" in src and "26 * 1024 * 1024" in src
