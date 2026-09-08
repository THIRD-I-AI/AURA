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
    """While PipelineAgent awaits its (slow, synchronous) LLM call, a
    concurrently-scheduled coroutine must still make progress. If the LLM
    call runs directly on the event loop (no to_thread offload), the ticker
    below stalls for the full duration of the LLM call."""

    agent = PipelineAgent()
    agent._llm = _SlowSyncLLM(delay=0.3)
    ctx = AgentContext(
        user_prompt="build a nightly load pipeline",
        task_description="build a nightly load pipeline",
        schema_context={},
    )

    tick_count = 0

    async def ticker():
        nonlocal tick_count
        for _ in range(20):
            await asyncio.sleep(0.02)
            tick_count += 1

    async def runner():
        await asyncio.gather(agent.execute(ctx), ticker())

    # Prime the default ThreadPoolExecutor: its first-ever to_thread/
    # run_in_executor call in a process pays a one-time worker-thread
    # startup cost, which would otherwise land inside the timed window
    # below and make the "concurrent, not serial" assertion flaky.
    asyncio.run(asyncio.to_thread(lambda: None))

    start = time.perf_counter()
    asyncio.run(runner())
    elapsed = time.perf_counter() - start

    # If the blocking call ran on the event loop, the ticker would be
    # starved and finish far fewer than 20 ticks in that window.
    assert tick_count == 20, (
        f"event loop was blocked: only {tick_count}/20 ticker iterations ran "
        "while the LLM call was in flight"
    )
    # Sanity: both coroutines ran concurrently, not serially. Serial execution
    # would take >= 0.3 (LLM) + 0.4 (ticker: 20 * 0.02) = 0.7s; concurrent
    # execution is bounded by the slower one (~0.3-0.4s) plus scheduling slop.
    assert elapsed < 0.65


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
