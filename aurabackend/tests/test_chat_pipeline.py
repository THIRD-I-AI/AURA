"""
AURA Chat Pipeline Integration Tests
=====================================
Tests the critical path: gateway health, chat endpoint, file upload,
connections, dashboard stats, query history.
Uses FastAPI TestClient for in-process testing (no running services needed).
"""

import os
import sys

import pytest

# Ensure the aurabackend package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Fixture: TestClient ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Create a FastAPI TestClient for the API gateway."""
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    return TestClient(app)


# ── Health ───────────────────────────────────────────────────────────

def test_health_returns_200(client):
    """Gateway /health should always return 200."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "api_gateway"


def test_root_returns_message(client):
    """Root / should return a welcome message."""
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "AURA" in data["message"]
    assert "version" in data


# ── Dashboard ────────────────────────────────────────────────────────

def test_dashboard_stats(client):
    """GET /dashboard/stats should return metrics."""
    resp = client.get("/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_rows" in data
    assert "active_sources" in data
    assert "queries_run" in data
    assert "system_health" in data


# ── Connections ──────────────────────────────────────────────────────

def test_list_connections(client):
    """GET /connections should return a list."""
    resp = client.get("/connections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert isinstance(data["connections"], list)


def test_create_and_delete_connection(client):
    """POST /connections creates, DELETE removes."""
    # Create
    resp = client.post("/connections", json={
        "name": "test-pg",
        "type": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "testdb",
        "username": "user",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    conn_id = data["connection"]["id"]

    # Delete
    resp = client.delete(f"/connections/{conn_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ── Connectors ───────────────────────────────────────────────────────

def test_available_connectors(client):
    """GET /connectors/available lists supported connectors."""
    resp = client.get("/connectors/available")
    assert resp.status_code == 200
    data = resp.json()
    assert "connectors" in data
    assert len(data["connectors"]) >= 3


# ── Query History ────────────────────────────────────────────────────

def test_query_history(client):
    """GET /query-history should return a list."""
    resp = client.get("/query-history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert isinstance(data["queries"], list)


def test_save_and_retrieve_query_history(client):
    """POST then GET query history."""
    client.post("/query-history", json={
        "prompt": "test prompt",
        "sql": "SELECT 1",
        "status": "success",
        "rows": 1,
        "executionTime": 10,
    })
    resp = client.get("/query-history")
    data = resp.json()
    assert data["total"] >= 1
    assert data["queries"][0]["prompt"] == "test prompt"


# ── Chat History ─────────────────────────────────────────────────────

def test_chat_history_roundtrip(client):
    """POST then GET chat history for a session."""
    session = "test-session-123"
    client.post(f"/chat/history/{session}", json={
        "type": "user",
        "content": "Hello AURA",
    })
    resp = client.get(f"/chat/history/{session}")
    assert resp.status_code == 200
    messages = resp.json()
    assert isinstance(messages, list)
    assert len(messages) >= 1
    assert messages[0]["content"] == "Hello AURA"


# ── File Formats ─────────────────────────────────────────────────────

def test_supported_file_formats(client):
    """GET /files/supported-formats should list formats."""
    resp = client.get("/files/supported-formats")
    assert resp.status_code == 200
    data = resp.json()
    assert "csv" in data["supported_formats"]
    assert "parquet" in data["supported_formats"]


# ── Job Control ──────────────────────────────────────────────────────

def test_approve_job(client):
    """POST /jobs/{id}/approve should work."""
    resp = client.post("/jobs/fake-job-123/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_cancel_job(client):
    """POST /jobs/{id}/cancel should work."""
    resp = client.post("/jobs/fake-job-123/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


# ── Validation ───────────────────────────────────────────────────────

def test_validate_safe_query(client):
    """POST /validate/query with a safe query."""
    resp = client.post("/validate/query", json={
        "query": "SELECT * FROM users LIMIT 10",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
