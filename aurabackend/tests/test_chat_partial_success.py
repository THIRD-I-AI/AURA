"""BUG-334: when the SQL ran but a later step failed (the chart or narrative -- e.g. a
provider 429 on the analysis call), POST /chat answered status "Error", and both chat
panels then hid the rows the query had returned."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agents.schemas import ExecutionOutput, NodeError, OrchestratorState, SQLGenOutput
from api_gateway.routers import chat


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    get_storage_backend().write("default", "sales.csv", b"region,amount\nEU,10\nUS,20\n")
    from api_gateway.main import app

    yield TestClient(app)
    reset_storage_backend()


def _state(errors, executed=True) -> OrchestratorState:
    return OrchestratorState(
        user_prompt="total sales by region", session_id="s1",
        sql=SQLGenOutput(sql="SELECT region, SUM(amount) AS total FROM sales GROUP BY region"),
        execution=ExecutionOutput(
            columns=["region", "total"], records=[{"region": "EU", "total": 10}, {"region": "US", "total": 20}],
            rows=[["EU", 10], ["US", 20]], row_count=2,
        ) if executed else None,
        errors=errors,
    )


def _ask(client, monkeypatch, state):
    from agents.base import AgentResult, AgentStatus

    async def _fake_orchestrator(*args, **kwargs):
        return state

    async def _sql_intent(self, ctx):  # no LLM call from the test
        return AgentResult(status=AgentStatus.SUCCESS, output={"intent": "sql"})

    monkeypatch.setattr(chat, "run_orchestrator", _fake_orchestrator)
    monkeypatch.setattr(chat.IntentAgent, "execute", _sql_intent)
    return client.post("/api/v1/chat", json={"message": "total sales by region", "session_id": "s1"})


def test_a_failed_narrative_after_a_successful_query_is_still_a_success(client, monkeypatch):
    resp = _ask(client, monkeypatch, _state([NodeError(node="analysis", message="rate limited (429)")]))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Success"
    assert body["execution_result"]["success"] is True
    assert body["execution_result"]["row_count"] == 2
    assert "analysis failed" in (body["error_message"] or "")  # the user is still told what did not run


def test_a_query_that_did_not_run_is_still_an_error(client, monkeypatch):
    resp = _ask(client, monkeypatch, _state([NodeError(node="sql_gen", message="no SQL")], executed=False))

    assert resp.json()["status"] == "Error"
