"""
ultracode-review finding — POST /uasr/baseline's DSR-009 martingale
re-baseline call (``_mapek_worker._martingale.register_baseline``) ran
directly on the event loop, unlike the classical-detector registration two
lines above it which is already offloaded via ``asyncio.to_thread``. A slow
rebuild (many columns/samples) would block every concurrent request on the
single uvicorn worker.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import uasr.service as service
from uasr.service import BaselineRequest, register_baseline


class _SlowMartingale:
    def __init__(self):
        self.called_with = None

    def register_baseline(self, source_id, baselines):
        time.sleep(0.3)  # simulates an expensive per-column detector rebuild
        self.called_with = (source_id, baselines)


class _FakeWorker:
    def __init__(self, martingale):
        self._martingale = martingale


@pytest.mark.asyncio
async def test_baseline_endpoint_offloads_martingale_rebaseline(monkeypatch):
    martingale = _SlowMartingale()
    monkeypatch.setattr(service, "_mapek_worker", _FakeWorker(martingale))

    heartbeat = {"ticks": 0}
    stop_heartbeat = asyncio.Event()

    async def _heartbeat():
        while not stop_heartbeat.is_set():
            heartbeat["ticks"] += 1
            await asyncio.sleep(0.01)

    hb_task = asyncio.create_task(_heartbeat())
    try:
        req = BaselineRequest(
            source_id="src_offload_test",
            rows=[{"metric": float(i)} for i in range(30)],
        )
        result = await register_baseline(req)
    finally:
        stop_heartbeat.set()
        await hb_task

    assert result["status"] == "registered"
    assert martingale.called_with is not None
    assert martingale.called_with[0] == "src_offload_test"
    assert heartbeat["ticks"] >= 10, (
        f"only {heartbeat['ticks']} heartbeat ticks during the blocking "
        "martingale.register_baseline() call -- it is blocking the event "
        "loop instead of running in a thread"
    )
