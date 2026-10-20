"""An agent that records an error and leaves its status unset has failed, not succeeded."""
from __future__ import annotations

import pytest

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity


class _Agent(BaseAgent):
    name = "TestAgent"

    def __init__(self, body):
        super().__init__()
        self._body = body

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        self._body(result)
        return result


async def _run(body) -> AgentResult:
    return await _Agent(body).execute(AgentContext(user_prompt="q", task_description="t"))


@pytest.mark.asyncio
async def test_a_swallowed_error_is_a_failure_with_its_reason():
    result = await _run(lambda r: r.add_step(action="tool_error", output_summary="connectors unreachable",
                                             severity=Severity.ERROR))
    assert result.status == AgentStatus.FAILED
    assert "connectors unreachable" in result.error


@pytest.mark.asyncio
async def test_no_error_is_still_a_success():
    result = await _run(lambda r: r.add_step(action="did_work", output_summary="ok"))
    assert result.status == AgentStatus.SUCCESS


@pytest.mark.asyncio
async def test_an_agent_that_decides_success_despite_errors_keeps_it():
    def body(r):
        r.add_step(action="one_source_failed", output_summary="b.csv: bad", severity=Severity.ERROR)
        r.status = AgentStatus.SUCCESS

    assert (await _run(body)).status == AgentStatus.SUCCESS


@pytest.mark.asyncio
async def test_an_ingest_where_some_sources_worked_still_succeeds():
    from agents.specialists.ingestion_agent import IngestionAgent

    class _Tools:
        async def call(self, name, **kwargs):
            if kwargs.get("filepath") == "bad.csv":
                raise RuntimeError("unreadable")
            return {"row_count": 3, "columns": ["a"]}

    ctx = AgentContext(user_prompt="ingest", task_description="ingest", files=["good.csv", "bad.csv"])
    result = await IngestionAgent(tool_registry=_Tools()).execute(ctx)
    assert result.status == AgentStatus.SUCCESS and result.output["total_sources"] == 1
