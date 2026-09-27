"""BUG-223: CORS must wrap the auth / rate-limit middleware, so their 401/429
responses still carry Access-Control-Allow-Origin. Starlette makes the LAST
added middleware the outermost; CORS used to be added before rate-limit/JWT,
which put it *inside* them."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ORIGIN = "http://localhost:5173"


@pytest.fixture()
def jwt_app(monkeypatch):
    from shared import service_factory

    monkeypatch.setattr(service_factory.settings, "jwt_enabled", True)
    monkeypatch.setattr(service_factory.settings, "cors_origins", [ORIGIN])
    app = service_factory.create_service(name="T", service_tag="t", version="1")

    @app.get("/api/v1/protected")
    def protected():
        return {"ok": True}

    return app


def test_cors_is_outside_auth_and_rate_limit(jwt_app):
    names = [m.cls.__name__ for m in jwt_app.user_middleware]  # outermost first
    cors = names.index("CORSMiddleware")
    for inner in ("JWTAuthMiddleware", "RateLimitMiddleware", "UploadBodyLimitMiddleware"):
        if inner in names:
            assert names.index(inner) > cors, names


def test_cross_origin_401_carries_cors_headers(jwt_app):
    resp = TestClient(jwt_app).get("/api/v1/protected", headers={"Origin": ORIGIN})
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == ORIGIN


def test_preflight_still_answered_and_security_headers_still_wrap_cors(jwt_app):
    resp = TestClient(jwt_app).options(
        "/api/v1/protected",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ORIGIN
    names = [m.cls.__name__ for m in jwt_app.user_middleware]
    assert names.index("SecurityHeadersMiddleware") < names.index("CORSMiddleware"), names
