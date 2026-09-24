"""BUG-169: StreamingEngine.stop() must not wait forever on a loop task that
swallows its cancellation.

stop() did `task.cancel(); await task`. If the task absorbs the CancelledError
and keeps looping, that await never returns. That is not hypothetical:
asyncio.wait_for on Python 3.11 can swallow a cancellation that races its inner
future completing (fixed in 3.12), and the engine's run loop calls
`asyncio.wait_for(self._buffer.get(), timeout=...)` on every tick. CI hit it: the
Python 3.11 backend job froze in test_engine_pause_resume -> engine.stop() until
the 20-minute faulthandler watchdog fired.

These tests do not depend on winning that race. Each loop is parked inside an
await that swallows the FIRST cancellation, exactly like the 3.11 behaviour, and
stop() must still finish.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.streaming.models import (  # noqa: E402
    StreamPipeline,
    StreamPipelineStatus,
    StreamSink,
    StreamSinkType,
    StreamSource,
    StreamSourceType,
    StreamTransform,
    TransformType,
    WindowConfig,
    WindowType,
)
from pipeline.streaming.streaming_engine import StreamingEngine  # noqa: E402


def _pipeline() -> StreamPipeline:
    return StreamPipeline(
        name="stop-test",
        source=StreamSource(
            type=StreamSourceType.SIMULATED,
            config={"events_per_second": 50, "event_type": "metric", "num_keys": 2},
        ),
        window=WindowConfig(type=WindowType.TUMBLING, size_seconds=5),
        sinks=[StreamSink(type=StreamSinkType.CONSOLE)],
        transforms=[StreamTransform(type=TransformType.KEY_BY, config={"field": "key"})],
        checkpoint_interval_seconds=60,
    )


def _blocks_and_swallows_first_cancel():
    """An awaitable that parks forever and swallows the first cancellation it
    receives, returning an empty batch as a completed wait_for would."""
    state = {"swallowed": 0}

    async def blocked(*_a, **_k):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            if state["swallowed"] == 0:
                state["swallowed"] += 1
                return []
            raise
        return []

    return blocked, state


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # checkpoints land in a throwaway dir


async def _stop_within(engine: StreamingEngine, seconds: float) -> None:
    """Fail if stop() has not finished on its own within `seconds`.

    Deliberately NOT asyncio.wait_for: on timeout that cancels stop(), and the
    old stop() swallowed a CancelledError meant for itself (its `except
    CancelledError: pass` around `await task` could not tell "the task I
    awaited was cancelled" from "I was cancelled"), carried on, and returned --
    so a wait_for-guarded test passed after burning the whole timeout."""
    stopping = asyncio.ensure_future(engine.stop())
    done, _ = await asyncio.wait({stopping}, timeout=seconds)
    if not done:
        stopping.cancel()
        raise AssertionError(f"engine.stop() had not finished after {seconds}s")
    await stopping


def test_stop_finishes_when_the_run_loop_swallows_a_cancellation():
    async def scenario():
        engine = StreamingEngine(_pipeline(), batch_size=20, tick_interval=0.05)
        await engine.start()
        blocked, state = _blocks_and_swallows_first_cancel()
        engine._backpressure.get_batch = blocked  # the run loop parks here next tick
        await asyncio.sleep(0.4)
        await _stop_within(engine, 5)
        return engine, state

    engine, state = asyncio.run(scenario())
    assert state["swallowed"] == 1, "the scenario never delivered a cancellation to the swallowing await"
    assert engine.pipeline.status == StreamPipelineStatus.STOPPED
    assert engine._task.done()


def test_stop_finishes_when_the_ingest_loop_swallows_a_cancellation():
    async def scenario():
        engine = StreamingEngine(_pipeline(), batch_size=20, tick_interval=0.05)
        await engine.start()
        blocked, state = _blocks_and_swallows_first_cancel()
        engine._source.read_batch = blocked  # the ingest loop parks here next tick
        await asyncio.sleep(0.4)
        await _stop_within(engine, 5)
        return engine, state

    engine, state = asyncio.run(scenario())
    assert state["swallowed"] == 1, "the scenario never delivered a cancellation to the swallowing await"
    assert engine.pipeline.status == StreamPipelineStatus.STOPPED
    assert engine._ingest_task.done()


def test_stop_after_pause_and_resume_still_stops_cleanly():
    """The shape of the CI hang (test_engine_pause_resume), on the happy path."""
    async def scenario():
        engine = StreamingEngine(_pipeline(), batch_size=20, tick_interval=0.05)
        await engine.start()
        await asyncio.sleep(0.2)
        await engine.pause()
        await asyncio.sleep(0.2)
        await engine.resume()
        await asyncio.sleep(0.2)
        await _stop_within(engine, 5)
        return engine

    engine = asyncio.run(scenario())
    assert engine.pipeline.status == StreamPipelineStatus.STOPPED
    assert engine._task.done() and engine._ingest_task.done()
