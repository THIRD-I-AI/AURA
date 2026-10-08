"""BUG-359: the chat ExecutionAgent read LLM-generated SQL results with fetchall(), so one
question over a large upload pulled every row into the single gateway worker's memory
and returned it all in one response. It is capped at AURA_QUERY_MAX_ROWS like /execute."""
from __future__ import annotations

import asyncio

import duckdb

from agents.base import AgentContext, AgentStatus
from agents.specialists.execution_agent import ExecutionAgent


def _run(sql: str, monkeypatch, cap: str):
    monkeypatch.setenv("AURA_QUERY_MAX_ROWS", cap)
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT range AS v FROM range(25)")
    ctx = AgentContext(user_prompt="all rows", task_description="exec",
                       upstream_results={"sql": sql}, metadata={"duckdb_con": con})
    return asyncio.run(ExecutionAgent().execute(ctx))


def test_a_result_larger_than_the_cap_is_cut_off_and_says_so(monkeypatch):
    res = _run("SELECT * FROM t", monkeypatch, cap="10")

    assert res.status == AgentStatus.SUCCESS
    assert len(res.output["records"]) == 10 and len(res.output["rows"]) == 10
    assert res.output["truncated"] is True


def test_a_result_within_the_cap_is_complete(monkeypatch):
    res = _run("SELECT * FROM t", monkeypatch, cap="25")

    assert len(res.output["records"]) == 25 and res.output["truncated"] is False


def test_a_query_that_outlives_the_agent_timeout_is_interrupted(monkeypatch):
    # BUG-363: the timeout cancelled the await but left the SQL running in its thread.
    import time

    monkeypatch.setenv("AURA_QUERY_MAX_ROWS", "10")
    con = duckdb.connect(":memory:")
    slow = "SELECT count(*) FROM range(1000000000) a, range(1000) b"
    ctx = AgentContext(user_prompt="q", task_description="exec", upstream_results={"sql": slow},
                       metadata={"duckdb_con": con}, timeout_seconds=1)

    started = time.monotonic()
    res = asyncio.run(ExecutionAgent().execute(ctx))
    assert res.status != AgentStatus.SUCCESS

    # the connection is free again almost at once: the runaway query was stopped
    assert con.execute("SELECT 42").fetchone()[0] == 42
    assert time.monotonic() - started < 10
