"""
IntentAgent async-safety regression test.

IntentAgent._run is on the hot path of every chat message and runs under a
single uvicorn worker (see .claude/rules/backend.md "Async safety"). Its LLM
call must be offloaded with asyncio.to_thread so a slow classifier call
doesn't freeze every concurrent request. This test proves the event loop
stays responsive while the (synchronous, blocking) LLM call is in flight.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentStatus
from agents.specialists.intent_agent import IntentAgent


class _SlowSyncLLM:
    """Mimics a synchronous LLM SDK call that blocks the calling thread."""

    def __init__(self, delay: float):
        self.delay = delay

    def generate_json(self, prompt: str):
        time.sleep(self.delay)  # simulates a blocking network/SDK call
        return {"intent": "conversation", "message": "hi"}


def test_intent_agent_does_not_block_event_loop():
    """While IntentAgent awaits its (slow, synchronous) LLM call, a
    concurrently-scheduled coroutine must still make progress. If the LLM
    call runs directly on the event loop (no to_thread offload), the ticker
    below stalls for the full duration of the LLM call."""

    agent = IntentAgent()
    agent.llm = _SlowSyncLLM(delay=0.3)
    ctx = AgentContext(
        user_prompt="hello there",
        task_description="classify intent",
        schema_context={},
    )

    # asyncio.gather always waits for BOTH coroutines to finish, so a plain
    # "did the ticker complete all 20 iterations" check passes whether or
    # not the LLM call blocks the loop. And once the ticker's Task has
    # actually started, its own `asyncio.sleep` calls keep it on a steady
    # ~0.02s cadence regardless — a synchronous blocking call runs to
    # completion as one uninterruptible chunk on whichever Task the loop
    # dispatches first, so the loop-blocking behaviour shows up as a
    # delayed START of the ticker, not as gaps once it's under way.
    # Measuring the delay before the first tick is what actually detects
    # the un-offloaded call (verified against both the buggy and fixed
    # implementations while writing this test).
    tick_times: list[float] = []

    async def ticker():
        loop = asyncio.get_event_loop()
        for _ in range(20):
            await asyncio.sleep(0.02)
            tick_times.append(loop.time())

    async def runner():
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        await asyncio.gather(agent.execute(ctx), ticker())
        return t0

    t0 = asyncio.run(runner())

    assert len(tick_times) == 20
    first_tick_delay = tick_times[0] - t0
    # Offloaded: the ticker starts promptly (~0.02s + scheduling slop).
    # Un-offloaded: the 0.3s synchronous LLM call runs to completion before
    # the loop ever dispatches the ticker's first iteration, so the first
    # tick lands at ~0.3s+. Threshold sits well between the two.
    assert first_tick_delay < 0.15, (
        f"event loop was blocked: first ticker tick landed {first_tick_delay:.3f}s "
        "after start (expected ~0.02s) — the LLM call ran on the event loop "
        "instead of being offloaded via asyncio.to_thread"
    )


def test_intent_agent_classifies_via_generate_json():
    """Regression guard: the offload must still deliver a correct result."""

    class _FastLLM:
        def generate_json(self, prompt: str):
            return {"intent": "sql", "message": ""}

    agent = IntentAgent()
    agent.llm = _FastLLM()
    ctx = AgentContext(
        user_prompt="top products by revenue",
        task_description="classify intent",
        schema_context={},
    )

    result = asyncio.run(agent.execute(ctx))

    assert result.status == AgentStatus.SUCCESS
    assert result.output.get("intent") == "sql"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
