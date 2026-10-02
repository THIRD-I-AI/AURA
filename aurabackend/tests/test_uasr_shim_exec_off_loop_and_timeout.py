"""BUG-259 / BUG-260: shim code (generated, exec()'d) ran synchronously on the event
loop during validation, in apply_shims and in the canary router, and the configured
``sandbox_timeout_seconds`` was never applied. One slow or looping shim stalled every
request on the single uvicorn worker, with nothing to cut it off."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig
from uasr.shim_router import ShimRouter

BATCH = BatchPayload(source_id="src", batch_id="b1", columns=["v"], rows=[{"v": 1}])
SHIM = types.SimpleNamespace(shim_code="def transform(rows):\n    return rows\n")


def _loop(timeout: float = 30.0) -> RecoveryLoop:
    return RecoveryLoop(DriftDetector(), RecoveryLoopConfig(sandbox_timeout_seconds=timeout))


@pytest.mark.asyncio
async def test_validation_runs_the_shim_off_the_event_loop(monkeypatch):
    loop = _loop()
    threads: list = []

    def _recording_execute(shim_code, rows):
        threads.append(threading.current_thread())
        return []  # "empty output": validation returns right after the shim ran

    monkeypatch.setattr(loop, "_sandbox_execute", _recording_execute)

    result = await loop._validate_shim(SHIM, BATCH, None)

    assert result["passed"] is False
    assert len(threads) == 1 and threads[0] is not threading.current_thread()


@pytest.mark.asyncio
async def test_a_shim_that_never_finishes_is_cut_off_and_the_loop_stays_responsive(monkeypatch):
    loop = _loop(timeout=0.2)
    release = threading.Event()

    def _stuck(shim_code, rows):
        release.wait(timeout=5)  # a runaway shim
        return rows

    monkeypatch.setattr(loop, "_sandbox_execute", _stuck)
    ticks = 0

    async def _heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    started = time.monotonic()
    try:
        result = await loop._validate_shim(SHIM, BATCH, None)
    finally:
        beat.cancel()
        release.set()

    assert result["passed"] is False and "did not finish" in result["reason"]
    assert time.monotonic() - started < 2.0, "the configured timeout was not applied"
    assert ticks >= 5, "the event loop was blocked while the shim ran"


@pytest.mark.asyncio
async def test_the_canary_router_runs_a_sync_transform_off_the_event_loop():
    router = ShimRouter()
    threads: list = []

    def _transform(source_id, rows):
        threads.append(threading.current_thread())
        return rows

    await router.add_route("src", "v1", _transform)

    await router.apply("src", [{"v": 1}])

    assert len(threads) == 1 and threads[0] is not threading.current_thread()


@pytest.mark.asyncio
async def test_an_async_transform_is_still_awaited():
    router = ShimRouter()

    async def _transform(source_id, rows):
        return [{"v": 2}]

    await router.add_route("src", "v1", _transform)
    out = await router.apply("src", [{"v": 1}])
    assert (out["rows"] if isinstance(out, dict) else out) == [{"v": 2}]
