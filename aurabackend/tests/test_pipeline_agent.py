"""
PipelineAgent async-safety regression test.

PipelineAgent._design_pipeline calls the LLM synchronously (see
.claude/rules/backend.md "Async safety"). Under the single-uvicorn-worker
deployment, a slow pipeline-design LLM turn must not stall every other
tenant's concurrent request. This test proves the event loop stays
responsive while the (synchronous, blocking) LLM call is in flight.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentStatus
from agents.specialists.pipeline_agent import PipelineAgent


class _SlowSyncLLM:
    """Mimics a synchronous LLM SDK call that blocks the calling thread."""

    def __init__(self, delay: float):
        self.delay = delay

    def is_available(self):
        return True

    def generate_json(self, prompt: str):
        time.sleep(self.delay)  # simulates a blocking network/SDK call
        return {
            "name": "nightly_load",
            "description": "load and clean data",
            "schedule": None,
            "steps": [{"id": "step_1", "action": "ingest",
                       "description": "ingest", "sql": "", "depends_on": []}],
        }


def test_pipeline_agent_does_not_block_event_loop():
    """PipelineAgent._design_pipeline must dispatch the (slow, synchronous)
    LLM call via asyncio.to_thread rather than running it directly on the
    event loop -- see .claude/rules/backend.md "Async safety".

    This used to be a wall-clock test (run a concurrent ticker coroutine
    alongside agent.execute() and assert the whole thing finished under a
    fixed time bound). That flaked in CI under load (BUG-094: observed
    0.6687s against a 0.65s bound) for the same structural reason as
    BUG-077 (pipeline/generator.py's identical pattern): asyncio.gather()
    waits for BOTH coroutines to finish regardless of ordering, so a blocked
    event loop only delays when the ticker's iterations happen, it doesn't
    prevent them from eventually completing -- the tick_count==20 assertion
    never actually caught a regression, leaving the tight wall-clock bound
    as the only real signal, close enough to the serial floor that ordinary
    scheduling jitter crossed it. Spying on asyncio.to_thread checks the
    real mechanism directly instead of inferring it from timing.
    """
    llm = _SlowSyncLLM(delay=0.01)  # tiny: no longer load-bearing for the assertion
    agent = PipelineAgent()
    agent._llm = llm
    ctx = AgentContext(
        user_prompt="build a nightly load pipeline",
        task_description="build a nightly load pipeline",
        schema_context={},
    )

    real_to_thread = asyncio.to_thread
    offloaded = []

    async def spy_to_thread(func, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    with patch("agents.specialists.pipeline_agent.asyncio.to_thread", side_effect=spy_to_thread):
        result = asyncio.run(agent.execute(ctx))

    assert llm.generate_json in offloaded, (
        "the LLM call ran directly on the event loop instead of via asyncio.to_thread"
    )
    assert result is not None


def test_pipeline_agent_designs_via_generate_json():
    """Regression guard: the offload must still deliver a correct result."""

    class _FastLLM:
        def is_available(self):
            return True

        def generate_json(self, prompt: str):
            return {
                "name": "fast_pipeline",
                "description": "d",
                "schedule": None,
                "steps": [{"id": "step_1", "action": "ingest",
                           "description": "ingest", "sql": "", "depends_on": []}],
            }

    agent = PipelineAgent()
    agent._llm = _FastLLM()
    ctx = AgentContext(
        user_prompt="build a pipeline",
        task_description="build a pipeline",
        schema_context={},
    )

    result = asyncio.run(agent.execute(ctx))

    assert result.status == AgentStatus.SUCCESS
    assert result.output.get("pipeline", {}).get("name") == "fast_pipeline"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
