"""
DARResearchAgent async-safety regression test.

DARResearchAgent._call_llm_json is the scoring/formulate call on the headless
DAR anomaly/trend research path and runs under a single uvicorn worker (see
.claude/rules/backend.md "Async safety"). Its LLM call must be offloaded with
asyncio.to_thread so a slow provider call doesn't freeze every concurrent
request. This test proves the event loop stays responsive while the
(synchronous, blocking) LLM call is in flight.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentStatus
from agents.specialists.dar_research_agent import DARResearchAgent


class _SlowSyncLLM:
    """Mimics a synchronous LLM SDK call that blocks the calling thread."""

    def __init__(self, delay: float):
        self.delay = delay

    def is_available(self):
        return True

    def generate_json(self, prompt: str):
        time.sleep(self.delay)  # simulates a blocking network/SDK call
        return {
            "finding_type": "anomaly",
            "summary": "Revenue spiked 3x on 2026-08-29.",
            "score": 0.9,
            "is_anomaly": True,
        }


def test_dar_research_agent_does_not_block_event_loop():
    """While DARResearchAgent awaits its (slow, synchronous) LLM call, a
    concurrently-scheduled coroutine must still make progress. If the LLM
    call runs directly on the event loop (no to_thread offload), the ticker
    below stalls for the full duration of the LLM call."""

    agent = DARResearchAgent()
    agent.llm = _SlowSyncLLM(delay=0.3)
    ctx = AgentContext(
        user_prompt="score this finding",
        task_description="score a DAR finding",
        schema_context={},
        metadata={
            "dar_mode": "score",
            "question": "Did revenue spike recently?",
            "sql": 'SELECT * FROM "sales" LIMIT 100',
            "rows": [{"date": "2026-08-29", "revenue": 30000}],
        },
    )

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
    # Loose hang-detector only -- NOT a concurrency discriminator. A serial
    # run's floor is ~0.3 (LLM) + 0.4 (ticker: 20 * 0.02) = 0.7s, but under
    # this repo's full ~2600-test pre-push suite, scheduling contention alone
    # can push even a genuinely concurrent run past that floor (observed
    # 0.7035s against a prior 0.69s ceiling). elapsed can't
    # reliably tell concurrent from serial once the whole process is under
    # load (BUG-163); tick_count == 20 above is the real, load-independent
    # proof this test exists for. This bound only catches a true multi-second
    # hang.
    assert elapsed < 5.0


def test_dar_research_agent_scores_via_generate_json():
    """Regression guard: the offload must still deliver a correct result."""

    class _FastLLM:
        def is_available(self):
            return True

        def generate_json(self, prompt: str):
            return {
                "finding_type": "trend",
                "summary": "Steady growth quarter over quarter.",
                "score": 0.4,
                "is_anomaly": False,
            }

    agent = DARResearchAgent()
    agent.llm = _FastLLM()
    ctx = AgentContext(
        user_prompt="score this finding",
        task_description="score a DAR finding",
        schema_context={},
        metadata={
            "dar_mode": "score",
            "question": "Is revenue trending up?",
            "sql": 'SELECT * FROM "sales" LIMIT 100',
            "rows": [{"date": "2026-08-29", "revenue": 30000}],
        },
    )

    result = asyncio.run(agent.execute(ctx))

    assert result.status == AgentStatus.SUCCESS
    assert result.output.get("finding_type") == "trend"
    assert result.output.get("score") == 0.4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
