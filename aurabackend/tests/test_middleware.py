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
from shared.exceptions import NotFoundError, ValidationError
from shared.middleware import (
    APIKeyMiddleware,
    JWTAuthMiddleware,
    RequestIDMiddleware,
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

    # BUG-005 (docs/BUG_REGISTRY.md): external cryptographic verification
    # routes, coded and commented as intentionally public, that returned
    # 401 in production because _PUBLIC_PATHS never listed them and every
    # prior test of this exempted them by never installing the middleware
    # at all (AURA_JWT_ENABLED defaults False). These routes exist here so
    # TestJWTMiddleware -- which genuinely installs the middleware, unlike
    # that other gap -- can assert the real behaviour.
    @app.get("/api/v1/counterfactual/jwks")
    def jwks():
        return {"keys": []}

    @app.get("/api/v1/counterfactual/audit/sth")
    def sth():
        return {"tree_size": 0}

    @app.get("/api/v1/counterfactual/audit/inclusion/{proof_hash}")
    def inclusion(proof_hash: str):
        return {"proof_hash": proof_hash}

    @app.get("/api/v1/counterfactual/audit/financial")
    def financial_audit():
        """NOT public -- sibling route under the same prefix, must still
        require auth so the fix doesn't over-broaden past what BUG-005
        actually asked for."""
        return {"ok": True}

    # BUG-017 (docs/BUG_REGISTRY.md): the inbound-hooks public trigger is
    # gated only by its own optional per-hook HMAC secret, checked inside
    # the handler -- not an AURA Bearer token, since the external systems
    # that fire it have no AURA login. Stand-in for the real handler in
    # inbound_hooks.py::fire_hook: no hook named "unknown-slug" exists, so
    # reaching this body (a 404, not a 401) proves the request got past
    # JWTAuthMiddleware to the HMAC-gated logic.
    @app.post("/api/v1/hooks/fire/{slug}")
    def fire_hook_stub(slug: str):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Hook not found")

    # BUG-037 (docs/BUG_REGISTRY.md): OIDC SSO login-flow routes are reached
    # by a browser with no AURA JWT yet -- each handler was already coded as
    # intentionally unauthenticated, but _PUBLIC_PATHS never caught up.
    @app.get("/api/v1/auth/oidc/status")
    def oidc_status_stub():
        return {"enabled": False}

    @app.get("/api/v1/auth/oidc/login")
    def oidc_login_stub():
        return {"redirect": "https://idp.example/authorize"}

    @app.get("/api/v1/auth/oidc/callback")
    def oidc_callback_stub():
        return {"ok": True}

    @app.post("/api/v1/auth/oidc/exchange")
    def oidc_exchange_stub():
        return {"access_token": "stub", "token_type": "bearer"}

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

    def test_jwks_exempt(self, client):
        """BUG-005 (docs/BUG_REGISTRY.md): shared _is_public_path helper,
        so the API-key gate must exempt it too."""
        resp = client.get("/api/v1/counterfactual/jwks")
        assert resp.status_code == 200


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

    # ── BUG-005 regression: real fix, verified with the middleware
    # genuinely installed (not the "auth defaults off" gap that let the
    # bug ship in the first place) ──────────────────────────────────

    def test_jwks_exempt_without_a_token(self, client):
        resp = client.get("/api/v1/counterfactual/jwks")
        assert resp.status_code == 200

    def test_sth_exempt_without_a_token(self, client):
        resp = client.get("/api/v1/counterfactual/audit/sth")
        assert resp.status_code == 200

    def test_inclusion_proof_exempt_without_a_token(self, client):
        resp = client.get(f"/api/v1/counterfactual/audit/inclusion/{'a' * 64}")
        assert resp.status_code == 200

    # ── BUG-017 regression: real fix, verified with JWTAuthMiddleware
    # genuinely installed. Before the fix this returned 401
    # AUTHENTICATION_REQUIRED and never reached the handler at all. ──

    def test_fire_hook_reaches_handler_without_a_token(self, client):
        resp = client.post("/api/v1/hooks/fire/unknown-slug")
        assert resp.status_code != 401
        assert resp.status_code == 404

    def test_fire_hook_is_not_gated_by_a_bearer_token(self, client):
        """A caller with no Authorization header at all must reach the
        HMAC-gated handler logic, not get rejected by JWTAuthMiddleware --
        that IS the bug: external systems firing this hook have no AURA
        login to present."""
        resp = client.post(
            "/api/v1/hooks/fire/unknown-slug",
            headers={"Authorization": ""},
        )
        assert resp.status_code != 401

    def test_sibling_financial_audit_route_still_requires_a_token(self, client):
        """The prefix-matched exemption must not leak past the one
        deliberately-public parameterized route."""
        resp = client.get("/api/v1/counterfactual/audit/financial")
        assert resp.status_code == 401

    # ── BUG-037 regression: real fix, verified with JWTAuthMiddleware
    # genuinely installed. Before the fix every one of these returned 401
    # AUTHENTICATION_REQUIRED and never reached the handler -- unreachable
    # SSO on any deployment with JWT auth armed (the production default). ─

    def test_oidc_status_reachable_without_a_token(self, client):
        resp = client.get("/api/v1/auth/oidc/status")
        assert resp.status_code == 200

    def test_oidc_login_reachable_without_a_token(self, client):
        resp = client.get("/api/v1/auth/oidc/login")
        assert resp.status_code == 200

    def test_oidc_callback_reachable_without_a_token(self, client):
        resp = client.get("/api/v1/auth/oidc/callback")
        assert resp.status_code == 200

    def test_oidc_exchange_reachable_without_a_token(self, client):
        resp = client.post("/api/v1/auth/oidc/exchange")
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
