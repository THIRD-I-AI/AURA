"""
PipelineGenerator async-safety regression test.

PipelineGenerator.generate() falls back to the LLM (shared.llm_provider.
LLMProvider, a plain synchronous client) for prompts the local rule-based
parser can't handle (see .claude/rules/backend.md "Async safety"). Under the
single-uvicorn-worker deployment, a slow LLM turn must not stall every other
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

from pipeline.generator import PipelineGenerator


def _fast_pipeline_json():
    return {
        "name": "ai_pipeline",
        "description": "d",
        "source": {"type": "file", "file_name": "data.csv"},
        "sink": {"type": "preview"},
        "steps": [{"type": "custom_sql", "description": "step", "config": {}}],
    }


class _SlowSyncLLM:
    """Mimics a synchronous LLM SDK call (Groq/Gemini/Ollama/OpenAI HTTP
    round-trip) that blocks the calling thread."""

    def __init__(self, delay: float):
        self.delay = delay

    def is_available(self):
        return True

    def generate_json(self, messages):
        time.sleep(self.delay)  # simulates a blocking network/SDK call
        return _fast_pipeline_json()

    def generate(self, messages):
        time.sleep(self.delay)
        return None


def _make_generator(llm) -> PipelineGenerator:
    gen = PipelineGenerator.__new__(PipelineGenerator)
    gen._llm = llm
    from pipeline.local_parser import LocalPipelineParser
    gen._local_parser = LocalPipelineParser()
    return gen


def test_pipeline_generator_does_not_block_event_loop():
    """While generate() awaits its (slow, synchronous) LLM call, a
    concurrently-scheduled coroutine must still make progress. If the LLM
    call runs directly on the event loop (no to_thread offload), the ticker
    below stalls for the full duration of the LLM call."""

    gen = _make_generator(_SlowSyncLLM(delay=0.3))
    # A prompt the local rule-based parser can't handle, forcing Tier 2 (LLM).
    prompt = "asdkjhasd qwoiuye lasdkjqwoi unmatched gibberish 12938"

    tick_count = 0

    async def ticker():
        nonlocal tick_count
        for _ in range(20):
            await asyncio.sleep(0.02)
            tick_count += 1

    async def runner():
        await asyncio.gather(gen.generate(prompt), ticker())

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


def test_pipeline_generator_generates_via_generate_json():
    """Regression guard: the offload must still deliver a correct result."""

    class _FastLLM:
        def is_available(self):
            return True

        def generate_json(self, messages):
            return _fast_pipeline_json()

        def generate(self, messages):
            return None

    gen = _make_generator(_FastLLM())
    prompt = "asdkjhasd qwoiuye lasdkjqwoi unmatched gibberish 12938"

    pipeline = asyncio.run(gen.generate(prompt))

    assert pipeline.name == "ai_pipeline"
    assert len(pipeline.steps) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
