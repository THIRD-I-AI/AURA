"""
AURA Service Factory Tests
============================
Tests for the create_service() factory function.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCreateService:
    @pytest.fixture()
    def app(self):
        from shared.service_factory import create_service
        return create_service(
            name="Test Service",
            service_tag="test_svc",
            version="1.0.0-test",
        )

    @pytest.fixture()
    def client(self, app):
        return TestClient(app)

    def test_health_endpoint_exists(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "test_svc"
        assert data["version"] == "1.0.0-test"

    def test_cors_headers_present(self, client):
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" in resp.headers

    def test_request_id_header_added(self, client):
        resp = client.get("/health")
        assert "X-Request-ID" in resp.headers

    def test_security_headers_present(self, client):
        # Sec-4: every response carries the OWASP-recommended minimum
        # set of defensive headers. HSTS is gated on is_production so
        # it should be absent in the test fixture's dev defaults.
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        # Dev fixture → no HSTS.
        assert "Strict-Transport-Security" not in resp.headers

    def test_security_headers_present_on_error_response(self, app):
        # Sec-4.1: when the route handler raises and the global
        # exception handler produces a JSONResponse, that response
        # bypasses the SecurityHeadersMiddleware.dispatch path because
        # `await call_next(request)` raised. The exception handler
        # must re-apply the defensive headers itself.
        #
        # TestClient's default raise_server_exceptions=True would
        # re-raise the RuntimeError to pytest instead of returning the
        # 500 response. Use raise_server_exceptions=False so we can
        # inspect the HTTP response + headers from the exception handler.
        from shared.exceptions import NotFoundError

        @app.get("/_explode_aura")
        def explode_aura():
            raise NotFoundError("Thing", "99")

        @app.get("/_explode_unhandled")
        def explode_unhandled():
            raise RuntimeError("forced for header test")

        error_client = TestClient(app, raise_server_exceptions=False)

        # AuraError path (4xx — TestClient returns normally either way)
        resp_a = error_client.get("/_explode_aura")
        assert resp_a.status_code == 404
        assert resp_a.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp_a.headers.get("X-Frame-Options") == "DENY"
        assert resp_a.headers.get("Referrer-Policy") == "no-referrer"

        # Unhandled-exception path (5xx — needs raise_server_exceptions=False)
        resp_u = error_client.get("/_explode_unhandled")
        assert resp_u.status_code == 500
        assert resp_u.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp_u.headers.get("X-Frame-Options") == "DENY"
        assert resp_u.headers.get("Referrer-Policy") == "no-referrer"

    def test_exception_handler_returns_json(self, app, client):
        from shared.exceptions import NotFoundError

        @app.get("/fail")
        def fail():
            raise NotFoundError("Thing", "99")

        resp = client.get("/fail")
        assert resp.status_code == 404
        assert resp.json()["error"] == "NOT_FOUND"
