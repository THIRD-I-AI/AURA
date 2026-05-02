"""
AURA End-to-End Chat Pipeline Test
====================================
Exercises the full golden path through the **LangGraph orchestrator**:

    POST /chat  →  IntentAgent (early-exit gate)
                →  run_orchestrator(skip_planner=True)
                     ├─ sql_run (SQLGeneratorAgent)
                     ├─ exec_run (ExecutionAgent → DuckDB)
                     ├─ viz_run (VisualizationAgent)
                     └─ analysis_run (AnalysisAgent)
                →  ChatResponse

Uses ``UnifiedMockLLM`` (tests/_mock_llm.py) so every test in the suite
shares the same provider-side instrumentation contract — the mock
populates ``_last_usage`` exactly like real Groq/Gemini calls do, so
the BATS / Audit / Prometheus observer chain is always exercised.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._mock_llm import UnifiedMockLLM, chat_happy_path, install_mock

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
def mock_llm(monkeypatch):
    """Install the unified mock so every BaseAgent.execute call resolves
    through the same instrumentation rails real providers use."""
    llm = chat_happy_path(table_name="_e2e_test_sales")
    install_mock(monkeypatch, llm)
    yield llm


@pytest.fixture()
def client(upload_dir, mock_llm):
    """FastAPI TestClient with mock LLM and real upload data."""
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    return TestClient(app)


V1 = "/api/v1"

# ── Tests ───────────────────────────────────────────────────────────

class TestE2EChatPipeline:
    """Full pipeline: NL question → SQL → DuckDB → structured response."""

    def test_chat_golden_path(self, client):
        """Happy path: question about uploaded data returns SQL + results."""
        resp = client.post(f"{V1}/chat", json={
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
        resp = client.post(f"{V1}/chat", json={
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
        resp = client.post(f"{V1}/chat", json={"message": ""})
        assert resp.status_code == 400

    def test_chat_with_session_id(self, client):
        """Session ID should be reflected in the response."""
        resp = client.post(f"{V1}/chat", json={
            "message": "total revenue",
            "session_id": "e2e-test-session-42",
        })
        data = resp.json()
        assert data["job_id"] == "job_e2e-test-session-42"

    def test_query_tracked_in_history(self, client):
        """After a successful chat, the query should appear in history."""
        # Execute a chat
        client.post(f"{V1}/chat", json={
            "message": "revenue by product",
        })

        # Check query history
        resp = client.get(f"{V1}/query-history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # Most recent query should contain our SQL
        latest = data["queries"][-1]
        assert "SELECT" in latest["sql"].upper()

    def test_metadata_includes_timestamp(self, client):
        """Response metadata should include a timestamp."""
        resp = client.post(f"{V1}/chat", json={"message": "show products"})
        data = resp.json()
        assert "metadata" in data
        assert "timestamp" in data["metadata"]
        assert "tables_loaded" in data["metadata"]
        assert data["metadata"]["tables_loaded"] >= 1


class TestChatRoutesThroughLangGraph:
    """Lock in that the chat router uses the unified LangGraph orchestrator
    path (Step 1 of the enterprise refactor) and that BaseAgent.execute is
    the single LLM entry point. Static and runtime assertions both — the
    static assertion catches a misguided revert; the runtime assertion
    catches a regression where the orchestrator silently falls back."""

    def test_chat_router_imports_run_orchestrator_not_dag_executor(self):
        """Chat router must use run_orchestrator from langgraph_orchestrator,
        and must NOT import the legacy DAGExecutor."""
        import api_gateway.routers.chat as chat_module
        from agents.langgraph_orchestrator import run_orchestrator

        assert chat_module.run_orchestrator is run_orchestrator, (
            "chat router's run_orchestrator is not the LangGraph one — "
            "the migration may have been reverted"
        )
        assert not hasattr(chat_module, "DAGExecutor"), (
            "chat router still references the legacy DAGExecutor — "
            "tech debt cleanup incomplete"
        )

    def test_chat_call_drives_run_orchestrator(self, client, mock_llm, monkeypatch):
        """Spy on run_orchestrator to confirm a /chat call actually invokes
        it with skip_planner=True (chat's signature for the canonical
        explicit-plan path). This is the runtime counterpart to the static
        import check above."""
        import api_gateway.routers.chat as chat_module

        seen_args: list = []
        original = chat_module.run_orchestrator

        async def _spy(*args, **kwargs):
            seen_args.append({"args": args, "kwargs": dict(kwargs)})
            return await original(*args, **kwargs)

        monkeypatch.setattr(chat_module, "run_orchestrator", _spy)

        resp = client.post(f"{V1}/chat", json={"message": "revenue by product"})
        assert resp.status_code == 200, resp.text

        assert len(seen_args) == 1, (
            f"expected exactly one run_orchestrator call, saw {len(seen_args)}"
        )
        kwargs = seen_args[0]["kwargs"]
        assert kwargs.get("skip_planner") is True, (
            "chat router must pass skip_planner=True (it builds the canonical path "
            f"itself, the planner is for open-ended agent flows). Got: {kwargs}"
        )
        assert kwargs.get("duckdb_con") is not None, (
            "chat router must inject duckdb_con so the orchestrator's exec_run "
            f"node can execute SQL. Got: {kwargs}"
        )

    def test_mock_populates_provider_usage_contextvar(self, client, mock_llm):
        """The unified mock must populate _last_usage like a real provider —
        otherwise the BATS / Audit / Prometheus observer chain falls back
        to estimation and the test suite no longer covers the production
        instrumentation path."""
        from shared.llm_provider import _OBSERVERS

        captured = []

        class _Spy:
            def on_call(self, ctx):
                captured.append({
                    "usage_source": ctx.usage_source,
                    "finish_reason": ctx.finish_reason,
                    "provider": ctx.provider,
                })

        spy = _Spy()
        _OBSERVERS.append(spy)
        try:
            resp = client.post(f"{V1}/chat", json={"message": "revenue by product"})
            assert resp.status_code == 200, resp.text
        finally:
            _OBSERVERS.remove(spy)

        assert captured, "no LLM calls were observed — orchestrator never invoked an agent"
        # Every captured call must look like a real-provider call —
        # usage_source="provider_reported" with a valid finish_reason.
        # If even one falls back to "estimated" the unified mock is
        # bypassing the production observer contract.
        bad = [c for c in captured if c["usage_source"] != "provider_reported"]
        assert not bad, (
            f"{len(bad)} mock call(s) bypassed the provider-usage contextvar — "
            f"the unified mock is broken: {bad[:3]}"
        )
        assert all(c["finish_reason"] == "stop" for c in captured), (
            f"unexpected finish_reason on mock call: {captured}"
        )


class TestE2EAuthIntegration:
    """Verify the auth endpoints work end-to-end with the gateway."""

    def test_token_roundtrip(self, client):
        """Issue a token then use it on /auth/me."""
        # Get a token
        resp = client.post(f"{V1}/auth/token", json={
            "user_id": "e2e-user",
            "email": "e2e@test.com",
            "role": "admin",
        })
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        # Use it
        resp = client.get(f"{V1}/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200
        me = resp.json()
        assert me["sub"] == "e2e-user"
        assert me["email"] == "e2e@test.com"
        assert me["role"] == "admin"

    def test_invalid_token_rejected(self, client):
        """A garbage token should get 401."""
        resp = client.get(f"{V1}/auth/me", headers={
            "Authorization": "Bearer not-a-real-token",
        })
        assert resp.status_code == 401
