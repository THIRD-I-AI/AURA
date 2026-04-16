"""
AURA Middleware Tests
======================
Tests for RequestID, Logging, APIKey, JWT, and exception handler middleware.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.auth import create_access_token
from shared.exceptions import AuthenticationError, NotFoundError, ValidationError
from shared.middleware import (
    APIKeyMiddleware,
    JWTAuthMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    register_exception_handlers,
)


def _make_app(**middleware_kwargs) -> FastAPI:
    """Build a minimal FastAPI app for middleware testing."""
    app = FastAPI()

    @app.get("/test")
    def test_endpoint():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    @app.get("/error/not-found")
    def raise_not_found():
        raise NotFoundError("Widget", "42")

    @app.get("/error/validation")
    def raise_validation():
        raise ValidationError("bad input")

    @app.get("/error/unhandled")
    def raise_unhandled():
        raise RuntimeError("boom")

    return app


# ── RequestID Middleware ───────────────────────────────────────────────

class TestRequestIDMiddleware:
    @pytest.fixture()
    def client(self):
        app = _make_app()
        app.add_middleware(RequestIDMiddleware)
        return TestClient(app)

    def test_generates_request_id(self, client):
        resp = client.get("/test")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
        # Should be a UUID-like string
        assert len(resp.headers["X-Request-ID"]) >= 32

    def test_reuses_client_request_id(self, client):
        resp = client.get("/test", headers={"X-Request-ID": "my-custom-id"})
        assert resp.headers["X-Request-ID"] == "my-custom-id"


# ── APIKey Middleware ──────────────────────────────────────────────────

class TestAPIKeyMiddleware:
    @pytest.fixture()
    def client(self):
        app = _make_app()
        app.add_middleware(APIKeyMiddleware, api_key="test-secret-key")
        return TestClient(app)

    def test_rejects_missing_key(self, client):
        resp = client.get("/test")
        assert resp.status_code == 401
        assert resp.json()["error"] == "UNAUTHORIZED"

    def test_rejects_wrong_key(self, client):
        resp = client.get("/test", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_accepts_correct_key(self, client):
        resp = client.get("/test", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200

    def test_health_exempt(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_options_exempt(self, client):
        resp = client.options("/test")
        # OPTIONS might return 405 but shouldn't be 401
        assert resp.status_code != 401


# ── JWT Middleware ─────────────────────────────────────────────────────

class TestJWTMiddleware:
    @pytest.fixture()
    def client(self):
        app = _make_app()
        app.add_middleware(JWTAuthMiddleware)

        @app.get("/whoami")
        def whoami(request):
            return {"user": getattr(request.state, "user", None)}

        return TestClient(app)

    def test_rejects_missing_token(self, client):
        resp = client.get("/test")
        assert resp.status_code == 401
        assert resp.json()["error"] == "AUTHENTICATION_REQUIRED"

    def test_rejects_invalid_token(self, client):
        resp = client.get("/test", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401

    def test_accepts_valid_token(self, client):
        token = create_access_token({"sub": "user-1", "role": "admin"})
        resp = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_health_exempt(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200


# ── Exception Handlers ─────────────────────────────────────────────────

class TestExceptionHandlers:
    @pytest.fixture()
    def client(self):
        app = _make_app()
        app.add_middleware(RequestIDMiddleware)
        register_exception_handlers(app)
        return TestClient(app, raise_server_exceptions=False)

    def test_not_found_returns_structured_json(self, client):
        resp = client.get("/error/not-found")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "NOT_FOUND"
        assert "Widget" in data["message"]
        assert "42" in data["message"]

    def test_validation_error_returns_422(self, client):
        resp = client.get("/error/validation")
        assert resp.status_code == 422
        assert resp.json()["error"] == "VALIDATION_ERROR"

    def test_unhandled_returns_500(self, client):
        resp = client.get("/error/unhandled")
        assert resp.status_code == 500
        data = resp.json()
        assert data["error"] == "INTERNAL_ERROR"
        # Should NOT leak the actual exception message
        assert "boom" not in data["message"]

    def test_error_includes_request_id(self, client):
        resp = client.get("/error/not-found", headers={"X-Request-ID": "trace-123"})
        data = resp.json()
        assert data.get("request_id") == "trace-123"
