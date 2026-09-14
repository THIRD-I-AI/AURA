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
from unittest.mock import patch

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
    """generate() must dispatch the (slow, synchronous) LLM call via
    asyncio.to_thread rather than running it directly on the event loop --
    see .claude/rules/backend.md "Async safety".

    This used to be a wall-clock test: run a concurrent ticker coroutine
    alongside gen.generate() and assert the whole thing finished under a
    fixed time bound tight enough to rule out the LLM call having run
    serially. That flaked in CI under load (observed 0.663s and 0.695s
    against a 0.65s bound, back-to-back) because asyncio.gather() waits for
    BOTH coroutines to finish regardless of ordering -- a blocked event loop
    only delays when the ticker's iterations happen, it doesn't prevent them
    from eventually completing, so elapsed time was the only signal actually
    catching a regression, and normal scheduling jitter was enough to cross
    a boundary that close to the serial-time floor (0.3 LLM + 0.4 ticker =
    0.7s). Spying on asyncio.to_thread checks the real mechanism directly
    instead of inferring it from timing.
    """
    llm = _SlowSyncLLM(delay=0.01)  # tiny: no longer load-bearing for the assertion
    gen = _make_generator(llm)
    # A prompt the local rule-based parser can't handle, forcing Tier 2 (LLM).
    prompt = "asdkjhasd qwoiuye lasdkjqwoi unmatched gibberish 12938"

    real_to_thread = asyncio.to_thread
    offloaded = []

    async def spy_to_thread(func, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    with patch("pipeline.generator.asyncio.to_thread", side_effect=spy_to_thread):
        pipeline = asyncio.run(gen.generate(prompt))

    assert llm.generate_json in offloaded, (
        "the LLM call ran directly on the event loop instead of via asyncio.to_thread"
    )
    assert pipeline is not None


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
