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

V1 = "/api/v1"


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
    resp = client.get(f"{V1}/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_rows" in data
    assert "active_sources" in data
    assert "queries_run" in data
    assert "system_health" in data


# ── Connections ──────────────────────────────────────────────────────

def test_list_connections(client):
    """GET /connections should return a list."""
    resp = client.get(f"{V1}/connections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert isinstance(data["connections"], list)


def test_create_and_delete_connection(client):
    """POST /connections creates, DELETE removes."""
    # Create
    resp = client.post(f"{V1}/connections", json={
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
    resp = client.delete(f"{V1}/connections/{conn_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ── Connectors ───────────────────────────────────────────────────────

def test_available_connectors(client):
    """GET /connectors/available lists supported connectors."""
    resp = client.get(f"{V1}/connectors/available")
    assert resp.status_code == 200
    data = resp.json()
    assert "connectors" in data
    assert len(data["connectors"]) >= 3


# ── Query History ────────────────────────────────────────────────────

def test_query_history(client):
    """GET /query-history should return a list."""
    resp = client.get(f"{V1}/query-history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert isinstance(data["queries"], list)


def test_save_and_retrieve_query_history(client):
    """POST then GET query history."""
    client.post(f"{V1}/query-history", json={
        "prompt": "test prompt",
        "sql": "SELECT 1",
        "status": "success",
        "rows": 1,
        "executionTime": 10,
    })
    resp = client.get(f"{V1}/query-history")
    data = resp.json()
    assert data["total"] >= 1
    assert data["queries"][0]["prompt"] == "test prompt"


# ── Chat History ─────────────────────────────────────────────────────

def test_chat_history_roundtrip(client):
    """POST then GET chat history for a session."""
    session = "test-session-123"
    client.post(f"{V1}/chat/history/{session}", json={
        "type": "user",
        "content": "Hello AURA",
    })
    resp = client.get(f"{V1}/chat/history/{session}")
    assert resp.status_code == 200
    messages = resp.json()
    assert isinstance(messages, list)
    assert len(messages) >= 1
    assert messages[0]["content"] == "Hello AURA"


# ── File Formats ─────────────────────────────────────────────────────

def test_supported_file_formats(client):
    """GET /files/supported-formats should list formats."""
    resp = client.get(f"{V1}/files/supported-formats")
    assert resp.status_code == 200
    data = resp.json()
    assert "csv" in data["supported_formats"]
    assert "parquet" in data["supported_formats"]


# ── Job Control ──────────────────────────────────────────────────────

def test_approve_job(client):
    """POST /jobs/{id}/approve should work."""
    resp = client.post(f"{V1}/jobs/fake-job-123/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_cancel_job(client):
    """POST /jobs/{id}/cancel should work."""
    resp = client.post(f"{V1}/jobs/fake-job-123/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


# ── Validation ───────────────────────────────────────────────────────

def test_validate_safe_query(client):
    """POST /validate/query with a safe query."""
    resp = client.post(f"{V1}/validate/query", json={
        "query": "SELECT * FROM users LIMIT 10",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True


# ── _humanize_pipeline_error ────────────────────────────────────────
# Raw provider exceptions (a multi-line Gemini 429 quota dump, an internal
# stack trace) must never reach the UI verbatim.

class TestHumanizePipelineError:
    def _fn(self):
        from api_gateway.routers.chat import _humanize_pipeline_error
        return _humanize_pipeline_error

    def test_rate_limit_dump_becomes_crisp(self):
        humanize = self._fn()
        raw = (
            "LLM error: 429 You exceeded your current quota. "
            "quota_metric: generativelanguage.googleapis.com/generate_content_free_tier "
            "violations { quota_id: GenerateRequestsPerMinutePerProjectPerModel-FreeTier }"
        )
        out = humanize(raw)
        assert "rate-limited" in out.lower()
        assert "quota_metric" not in out
        assert len(out) < 120

    def test_groq_size_limit_is_rate_limited_message(self):
        out = self._fn()("Groq rate/size limit: prompt too large (413)")
        assert "rate-limited" in out.lower()

    def test_no_llm_configured_message(self):
        out = self._fn()("No LLM provider available — install Ollama or set GROQ_API_KEY.")
        assert "no ai model is configured" in out.lower()
        assert "GROQ_API_KEY" in out

    def test_genuine_sql_error_is_preserved(self):
        # A real, actionable DB error must NOT be swallowed by the humanizer.
        raw = 'Catalog Error: Table "custmer" does not exist'
        out = self._fn()(raw)
        assert out == raw

    def test_long_unknown_error_is_capped(self):
        out = self._fn()("x" * 500)
        assert len(out) <= 240
        assert out.endswith("…")

    def test_none_message_safe(self):
        assert self._fn()(None) == "unknown error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
