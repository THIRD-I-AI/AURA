"""DPC integration into SQLGeneratorAgent — Tier A (LLM + tools faked)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentResult
from agents.specialists.sql_generator_agent import SQLGeneratorAgent
from agents.tool_registry import Tool, ToolRegistry

_TABLE = {"columns": ["g", "v"], "rows": [["a", 1], ["a", 2], ["b", 3]]}


class _RoutingLLM:
    """Returns SQL for the generation prompt and a pandas expr for the DPC prompt."""
    def __init__(self, sql, pandas_expr):
        self._sql, self._pandas = sql, pandas_expr

    def is_available(self):
        return True

    def generate(self, prompt, **kw):
        if "pandas DataFrame named" in prompt:
            return self._pandas
        if "Explain the following SQL" in prompt:
            return "returns the sum of v"
        return self._sql


def _tools(answer_rows):
    reg = ToolRegistry()

    async def _exec(*, query, connection_id="default"):
        q = query.strip()
        if q.upper().startswith("EXPLAIN"):
            return {"columns": [], "rows": []}
        if "SELECT * FROM" in q.upper():
            return _TABLE
        return {"columns": ["s"], "rows": answer_rows}

    reg.register(Tool(name="execute_sql", description="x", category="sql", fn=_exec))
    return reg


def _agent(answer_rows, sql, pandas_expr):
    agent = SQLGeneratorAgent(tool_registry=_tools(answer_rows))
    agent._llm = agent.llm = _RoutingLLM(sql, pandas_expr)
    return agent


def _ctx():
    return AgentContext(user_prompt="total v", task_description="total v",
                        schema_context={}, metadata={"execute": True})


def test_dpc_verified_annotation():
    agent = _agent([[6]], 'SELECT SUM("v") AS s FROM "t"', 'df["v"].sum()')
    res = asyncio.run(agent._run(_ctx(), AgentResult()))
    assert res.output["cross_verified"] is True
    assert res.output["verification"]["status"] == "verified"


def test_dpc_mismatch_after_retry_exhausted(monkeypatch):
    monkeypatch.setenv("AURA_DPC_MAX_RETRIES", "1")
    # SQL always returns 5; pandas always computes 6 → mismatch survives the retry.
    agent = _agent([[5]], 'SELECT SUM("v") AS s FROM "t"', 'df["v"].sum()')
    res = asyncio.run(agent._run(_ctx(), AgentResult()))
    assert res.output["cross_verified"] is False
    assert res.output["verification"]["status"] == "mismatch"


def test_dpc_disabled_no_annotation(monkeypatch):
    monkeypatch.setenv("AURA_DPC_ENABLED", "0")
    agent = _agent([[6]], 'SELECT SUM("v") AS s FROM "t"', 'df["v"].sum()')
    res = asyncio.run(agent._run(_ctx(), AgentResult()))
    assert "verification" not in res.output
    assert "cross_verified" not in res.output
