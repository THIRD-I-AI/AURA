"""
AURA End-to-End Chat Pipeline Test
====================================
Exercises the full golden path:

    POST /chat  →  IntentAgent  →  SQLGeneratorAgent  →  ExecutionAgent
                                                       →  VisualizationAgent
                                                       →  AnalysisAgent
                                 →  DuckDB execution   →  JSON response

Uses a deterministic mock LLM so the test is fast, offline, and reproducible.
A temporary CSV file is placed in the upload directory so `build_schema_context`
discovers real tables in DuckDB.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm_provider import LLMProvider


# ── Deterministic Mock LLM ──────────────────────────────────────────

class _MockLLM(LLMProvider):
    """Returns canned responses keyed on prompt content.

    - Intent classification → always returns ``{"intent": "sql"}``
    - SQL generation → returns ``SELECT * FROM <table> LIMIT 10``
    - Anything else → returns a generic JSON blob
    """

    provider_name = "mock"

    def __init__(self, table_name: str = "sales") -> None:
        super().__init__(model="mock-v1")
        self._table = table_name

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        text = prompt if isinstance(prompt, str) else "\n".join(prompt)

        # Intent agent
        if "intent" in text.lower() and "conversation" in text.lower():
            return json.dumps({"intent": "sql", "message": ""})

        # SQL generator
        if "sql" in text.lower() or "select" in text.lower() or "query" in text.lower():
            return f"SELECT Product, SUM(Revenue) as total_revenue FROM {self._table} GROUP BY Product"

        # Visualization agent
        if "chart" in text.lower() or "visual" in text.lower():
            return json.dumps({
                "chart_type": "bar",
                "x_axis": "Product",
                "y_axis": "total_revenue",
                "title": "Revenue by Product",
            })

        # Analysis agent
        if "analy" in text.lower() or "insight" in text.lower():
            return json.dumps({
                "conclusion": "Widget B generates the most revenue.",
                "confidence": 0.95,
            })

        # Default
        return json.dumps({"result": "ok"})


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def upload_dir():
    """Create a temp upload directory with a sample CSV, yield its path,
    then clean up."""
    base = Path(__file__).resolve().parent.parent
    uploads = base / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    csv_path = uploads / "_e2e_test_sales.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Product", "Revenue", "Quantity", "Region"])
        writer.writerow(["2024-01-01", "Widget A", "1000.50", "10", "North"])
        writer.writerow(["2024-01-01", "Widget B", "2000.75", "20", "South"])
        writer.writerow(["2024-01-02", "Widget A", "1500.00", "15", "East"])
        writer.writerow(["2024-01-02", "Widget B", "2500.25", "25", "West"])
        writer.writerow(["2024-01-03", "Widget A", "1200.00", "12", "North"])

    yield uploads
    csv_path.unlink(missing_ok=True)


@pytest.fixture()
def mock_llm():
    """Patch ``get_llm`` globally so every agent gets the mock."""
    llm = _MockLLM(table_name="_e2e_test_sales")
    with patch("shared.llm_provider.get_llm", return_value=llm), \
         patch("shared.llm_provider._cached_llm", llm):
        # Also patch at the import sites so already-imported references pick it up
        with patch("agents.specialists.intent_agent.get_llm", return_value=llm), \
             patch("agents.specialists.sql_generator_agent.get_llm", return_value=llm):
            yield llm


@pytest.fixture()
def client(upload_dir, mock_llm):
    """FastAPI TestClient with mock LLM and real upload data."""
    from fastapi.testclient import TestClient
    from api_gateway.main import app
    return TestClient(app)


# ── Tests ───────────────────────────────────────────────────────────

class TestE2EChatPipeline:
    """Full pipeline: NL question → SQL → DuckDB → structured response."""

    def test_chat_golden_path(self, client):
        """Happy path: question about uploaded data returns SQL + results."""
        resp = client.post("/chat", json={
            "message": "What is the total revenue by product?",
            "auto_execute": True,
        })
        assert resp.status_code == 200
        data = resp.json()

        # Pipeline completed
        assert data["status"] == "Success", f"Pipeline failed: {data.get('error_message')}"
        assert data["job_id"].startswith("job_")

        # SQL was generated
        assert data["final_query"] is not None
        assert "SELECT" in data["final_query"].upper()

        # Tables were discovered from the uploaded CSV
        assert len(data["available_tables"]) >= 1

        # Execution produced results
        exec_result = data.get("execution_result", {})
        assert exec_result.get("success") is True
        assert exec_result.get("row_count", 0) > 0
        assert len(exec_result.get("columns", [])) >= 1
        assert len(exec_result.get("data", [])) > 0

        # Timing metadata
        assert data["execution_time_ms"] > 0

    def test_chat_returns_columns_and_rows(self, client):
        """Verify the response shape has both columnar and row data."""
        resp = client.post("/chat", json={
            "message": "Show me revenue by product",
        })
        data = resp.json()
        assert data["status"] == "Success"

        exec_result = data["execution_result"]
        columns = exec_result["columns"]
        rows = exec_result["rows"]
        records = exec_result["data"]

        # Columns are strings
        assert all(isinstance(c, str) for c in columns)
        # Rows is a list of lists
        assert isinstance(rows, list)
        assert isinstance(rows[0], list)
        # Records is a list of dicts
        assert isinstance(records, list)
        assert isinstance(records[0], dict)
        # Columns match record keys
        assert set(columns) == set(records[0].keys())

    def test_chat_empty_message_returns_400(self, client):
        """Empty message should be rejected."""
        resp = client.post("/chat", json={"message": ""})
        assert resp.status_code == 400

    def test_chat_with_session_id(self, client):
        """Session ID should be reflected in the response."""
        resp = client.post("/chat", json={
            "message": "total revenue",
            "session_id": "e2e-test-session-42",
        })
        data = resp.json()
        assert data["job_id"] == "job_e2e-test-session-42"

    def test_query_tracked_in_history(self, client):
        """After a successful chat, the query should appear in history."""
        # Execute a chat
        client.post("/chat", json={
            "message": "revenue by product",
        })

        # Check query history
        resp = client.get("/query-history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # Most recent query should contain our SQL
        latest = data["queries"][-1]
        assert "SELECT" in latest["sql"].upper()

    def test_metadata_includes_timestamp(self, client):
        """Response metadata should include a timestamp."""
        resp = client.post("/chat", json={"message": "show products"})
        data = resp.json()
        assert "metadata" in data
        assert "timestamp" in data["metadata"]
        assert "tables_loaded" in data["metadata"]
        assert data["metadata"]["tables_loaded"] >= 1


class TestE2EAuthIntegration:
    """Verify the auth endpoints work end-to-end with the gateway."""

    def test_token_roundtrip(self, client):
        """Issue a token then use it on /auth/me."""
        # Get a token
        resp = client.post("/auth/token", json={
            "user_id": "e2e-user",
            "email": "e2e@test.com",
            "role": "admin",
        })
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        # Use it
        resp = client.get("/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200
        me = resp.json()
        assert me["sub"] == "e2e-user"
        assert me["email"] == "e2e@test.com"
        assert me["role"] == "admin"

    def test_invalid_token_rejected(self, client):
        """A garbage token should get 401."""
        resp = client.get("/auth/me", headers={
            "Authorization": "Bearer not-a-real-token",
        })
        assert resp.status_code == 401
