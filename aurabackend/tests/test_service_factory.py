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

    def test_exception_handler_returns_json(self, app, client):
        from shared.exceptions import NotFoundError

        @app.get("/fail")
        def fail():
            raise NotFoundError("Thing", "99")

        resp = client.get("/fail")
        assert resp.status_code == 404
        assert resp.json()["error"] == "NOT_FOUND"
