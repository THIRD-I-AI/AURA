"""
Regression test: SQLGeneratorAgent._explain_sql and _generate_sql must not
block the single uvicorn worker's event loop with a synchronous LLM call.

See .claude/rules/backend.md "Async safety" — a sync provider call made
directly inside an async handler freezes every concurrent request until it
returns. Both `_explain_sql` and `_generate_sql` (the primary, highest-traffic
LLM call in the app) must offload `self._llm.generate` via
`asyncio.to_thread` rather than calling it inline.
"""
import asyncio
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.specialists.sql_generator_agent import SQLGeneratorAgent


class _FakeLLM:
    """Records which thread `generate` ran on, so we can prove it was
    offloaded off the event-loop thread rather than called inline."""

    def __init__(self, text: str = "Returns rows from the table."):
        self.text = text
        self.call_thread = None
        self.calls = []

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str) -> str:
        self.call_thread = threading.current_thread()
        self.calls.append(prompt)
        return self.text


@pytest.fixture
def agent():
    a = SQLGeneratorAgent(tool_registry=None)
    a._llm = _FakeLLM()
    return a


async def test_explain_sql_offloads_blocking_call_to_thread(agent):
    """The sync LLM call must run on a worker thread, not the event-loop
    thread, so it cannot block other concurrent requests."""
    loop_thread = threading.current_thread()

    explanation = await agent._explain_sql("SELECT * FROM orders")

    assert explanation == "Returns rows from the table."
    assert len(agent._llm.calls) == 1
    assert "SELECT * FROM orders" in agent._llm.calls[0]
    assert agent._llm.call_thread is not None
    assert agent._llm.call_thread != loop_thread, (
        "self._llm.generate ran on the event-loop thread — it must be "
        "wrapped in asyncio.to_thread so it cannot block the sole worker."
    )


async def test_explain_sql_does_not_block_concurrent_task(agent):
    """End-to-end proof: while _explain_sql's LLM call is in flight, another
    coroutine scheduled on the same loop must still get to run."""
    release = threading.Event()
    started = threading.Event()

    def slow_generate(prompt: str) -> str:
        started.set()
        release.wait(timeout=5)
        return "slow explanation"

    agent._llm.generate = slow_generate

    tick_count = 0

    async def ticker():
        nonlocal tick_count
        # If _explain_sql were blocking the loop inline, this loop would
        # never get scheduled until the "slow" call finished.
        while not started.is_set():
            await asyncio.sleep(0)
        for _ in range(3):
            await asyncio.sleep(0.01)
            tick_count += 1
        release.set()

    explanation, _ = await asyncio.gather(
        agent._explain_sql("SELECT 1"),
        ticker(),
    )

    assert explanation == "slow explanation"
    assert tick_count == 3


async def test_explain_sql_returns_none_when_llm_unavailable(agent):
    agent._llm.is_available = lambda: False
    assert await agent._explain_sql("SELECT 1") is None
    assert agent._llm.calls == []


async def test_explain_sql_swallows_generate_exception(agent):
    def boom(prompt: str):
        raise RuntimeError("provider down")

    agent._llm.generate = boom
    assert await agent._explain_sql("SELECT 1") is None


# ── _generate_sql Async-Safety Tests ─────────────────────────────────
# _generate_sql is the primary, highest-traffic LLM call in the app (SQL
# generation) — see .claude/rules/backend.md "Async safety".

async def test_generate_sql_offloads_blocking_call_to_thread(agent):
    """The sync LLM call must run on a worker thread, not the event-loop
    thread, so it cannot block other concurrent requests."""
    loop_thread = threading.current_thread()
    agent._llm.text = "SELECT * FROM orders"

    sql, error = await agent._generate_sql("show me all orders", "{}")

    assert error is None
    assert sql == "SELECT * FROM orders"
    assert len(agent._llm.calls) == 1
    assert agent._llm.call_thread is not None
    assert agent._llm.call_thread != loop_thread, (
        "self._llm.generate ran on the event-loop thread — it must be "
        "wrapped in asyncio.to_thread so it cannot block the sole worker."
    )


async def test_generate_sql_does_not_block_concurrent_task(agent):
    """End-to-end proof: while _generate_sql's LLM call is in flight, another
    coroutine scheduled on the same loop must still get to run."""
    release = threading.Event()
    started = threading.Event()

    def slow_generate(prompt: str) -> str:
        started.set()
        release.wait(timeout=5)
        return "SELECT 1"

    agent._llm.generate = slow_generate

    tick_count = 0

    async def ticker():
        nonlocal tick_count
        # If _generate_sql were blocking the loop inline, this loop would
        # never get scheduled until the "slow" call finished.
        while not started.is_set():
            await asyncio.sleep(0)
        for _ in range(3):
            await asyncio.sleep(0.01)
            tick_count += 1
        release.set()

    (sql, error), _ = await asyncio.gather(
        agent._generate_sql("show me all rows", "{}"),
        ticker(),
    )

    assert error is None
    assert sql == "SELECT 1"
    assert tick_count == 3
