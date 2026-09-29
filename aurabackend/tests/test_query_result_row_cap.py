"""BUG-228: SQL over a tenant's uploads must not fetchall() an unbounded result."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _no_llm_analysis(monkeypatch):
    # /execute asks an LLM agent to explain non-empty results; keep the test offline.
    from agents.specialists import analysis_agent

    async def _skip(self, ctx):
        class R:
            succeeded = False
            output = {}
        return R()

    monkeypatch.setattr(analysis_agent.AnalysisAgent, "execute", _skip)


def test_execute_caps_rows_and_reports_truncation(monkeypatch):
    from api_gateway.main import app

    monkeypatch.setenv("AURA_QUERY_MAX_ROWS", "10")
    r = TestClient(app).post("/api/v1/execute", json={"sql": "SELECT * FROM range(25)"})
    body = r.json()
    assert body["success"] is True, body
    assert body["row_count"] == 10 and len(body["data"]) == 10
    assert body["truncated"] is True


def test_execute_under_the_cap_is_not_marked_truncated(monkeypatch):
    from api_gateway.main import app

    monkeypatch.setenv("AURA_QUERY_MAX_ROWS", "10")
    body = TestClient(app).post("/api/v1/execute", json={"sql": "SELECT * FROM range(10)"}).json()
    assert body["row_count"] == 10 and body["truncated"] is False


@pytest.mark.asyncio
async def test_saved_query_runner_is_capped_too(monkeypatch):
    from api_gateway.routers.queries import _execute_saved_query_sql

    monkeypatch.setenv("AURA_QUERY_MAX_ROWS", "7")
    res = await _execute_saved_query_sql("SELECT * FROM range(100)")
    assert res["row_count"] == 7 and res["truncated"] is True
