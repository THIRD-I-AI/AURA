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


def test_a_runaway_execute_query_is_stopped_at_the_timeout(monkeypatch):
    # BUG-381: /execute ran user SQL in a bare to_thread with no limit, holding a scarce
    # executor thread until DuckDB finished; it is now interrupted at the timeout.
    import time

    from api_gateway.main import app

    monkeypatch.setenv("AURA_QUERY_TIMEOUT_SECONDS", "1")
    started = time.monotonic()
    r = TestClient(app).post("/api/v1/execute", json={
        "sql": "SELECT count(*) FROM range(1000000000) a, range(1000) b"})
    assert r.json()["success"] is False
    assert time.monotonic() - started < 15


def test_run_interruptible_stops_the_query_and_frees_the_connection(monkeypatch):
    import asyncio

    import duckdb

    from shared.duckdb_factory import run_interruptible

    con = duckdb.connect(":memory:")
    slow = lambda: con.execute("SELECT count(*) FROM range(1000000000) a, range(1000) b").fetchall()  # noqa: E731
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run_interruptible(con, slow, timeout=0.5))
    assert con.execute("SELECT 1").fetchone()[0] == 1
