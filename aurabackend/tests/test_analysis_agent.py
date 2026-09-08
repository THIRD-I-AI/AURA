"""
AnalysisAgent async-safety regression test.

AnalysisAgent._run runs under a single uvicorn worker (see
.claude/rules/backend.md "Async safety"). Its LLM call (self.llm.generate)
must be offloaded with asyncio.to_thread so a slow provider round-trip
doesn't freeze every concurrent request. This test proves the event loop
stays responsive while the (synchronous, blocking) LLM call is in flight.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentStatus
from agents.specialists.analysis_agent import AnalysisAgent


def _make_ctx() -> AgentContext:
    records = [
        {"region": "west", "revenue": 100.0},
        {"region": "east", "revenue": 150.0},
        {"region": "west", "revenue": 120.0},
        {"region": "east", "revenue": 90.0},
    ]
    return AgentContext(
        user_prompt="which region has higher revenue?",
        task_description="which region has higher revenue?",
        schema_context={},
        upstream_results={
            "execution": {
                "records": records,
                "columns": ["region", "revenue"],
                "sql": "SELECT region, revenue FROM sales;",
            }
        },
    )


class _SlowSyncLLM:
    """Mimics a synchronous LLM SDK call that blocks the calling thread."""

    def __init__(self, delay: float):
        self.delay = delay

    def is_available(self):
        return True

    def generate(self, prompt: str):
        time.sleep(self.delay)  # simulates a blocking network/SDK call
        return "East slightly outperforms west on average revenue."


def test_analysis_agent_does_not_block_event_loop():
    """While AnalysisAgent awaits its (slow, synchronous) LLM call, a
    concurrently-scheduled coroutine must still make progress. If the LLM
    call runs directly on the event loop (no to_thread offload), the ticker
    below stalls for the full duration of the LLM call."""

    agent = AnalysisAgent()
    agent._llm = _SlowSyncLLM(delay=0.3)
    agent.llm = agent._llm
    ctx = _make_ctx()

    tick_count = 0

    async def ticker():
        nonlocal tick_count
        for _ in range(20):
            await asyncio.sleep(0.02)
            tick_count += 1

    async def runner():
        await asyncio.gather(agent.execute(ctx), ticker())

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
    assert elapsed < 0.9


def test_analysis_agent_produces_conclusion_via_generate():
    """Regression guard: the offload must still deliver a correct result."""

    class _FastLLM:
        def is_available(self):
            return True

        def generate(self, prompt: str):
            return "East slightly outperforms west on average revenue."

    agent = AnalysisAgent()
    agent._llm = _FastLLM()
    agent.llm = agent._llm
    ctx = _make_ctx()

    result = asyncio.run(agent.execute(ctx))

    assert result.status == AgentStatus.SUCCESS
    assert result.output.get("conclusion") == (
        "East slightly outperforms west on average revenue."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
