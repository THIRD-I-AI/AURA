"""BUG-437: an ingest in which every source fails must not be reported as a success."""
from __future__ import annotations

import pytest

from agents.base import AgentContext, AgentStatus
from agents.specialists.ingestion_agent import IngestionAgent


class _Tools:
    def __init__(self, fail: bool):
        self.fail = fail

    async def call(self, name, **kwargs):
        if self.fail:
            raise TypeError("ingest_and_profile() got an unexpected keyword argument 'filepath'")
        return {"rows": 3, "columns": ["a"]}


@pytest.mark.asyncio
async def test_an_ingest_where_every_file_fails_is_a_failure():
    ctx = AgentContext(user_prompt="ingest", task_description="ingest", files=["sales.csv"])
    result = await IngestionAgent(tool_registry=_Tools(fail=True)).execute(ctx)

    assert result.status == AgentStatus.FAILED
    assert "sales.csv" in (result.error or "")


@pytest.mark.asyncio
async def test_a_working_ingest_still_succeeds():
    ctx = AgentContext(user_prompt="ingest", task_description="ingest", files=["sales.csv"])
    result = await IngestionAgent(tool_registry=_Tools(fail=False)).execute(ctx)

    assert result.status == AgentStatus.SUCCESS
    assert result.output["total_sources"] == 1
