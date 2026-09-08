"""
Regression test — DiagnosticReflectorAgent._llm_diagnosis must not block the
single uvicorn worker's event loop while waiting on the synchronous LLM call.

Tier A (pure Python, no optional deps).

Context: aurabackend/uasr/reflector_agent.py `_llm_diagnosis` is the
LLM-assisted fallback path of the diagnosis step in the MAPE-K recovery loop
(invoked from recovery_loop.py's async `_diagnose`). `llm.generate_json` is a
synchronous network call; per .claude/rules/backend.md "Async safety" it must
be offloaded with `asyncio.to_thread` so it doesn't freeze every concurrent
request on the sole uvicorn worker for the duration of the LLM round-trip.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.models import DriftDetectionResult, DriftSeverity, DriftType
from uasr.reflector_agent import DiagnosticReflectorAgent


class _BlockingLLM:
    """Simulates a real provider's synchronous network round-trip via
    ``time.sleep`` (not ``asyncio.sleep``) — this is the actual failure
    shape: a sync call with no cooperative yield point."""

    def __init__(self, delay_seconds: float) -> None:
        self._delay = delay_seconds

    def is_available(self) -> bool:
        return True

    def generate_json(self, prompt):
        time.sleep(self._delay)
        return {
            "root_cause": "Simulated LLM diagnosis",
            "hypothesis": "test hypothesis",
            "suggested_action": "test action",
            "confidence": 0.7,
        }


def _make_drift() -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="src-1",
        batch_id="batch-1",
        drift_detected=True,
        drift_type=DriftType.SEMANTIC,
        severity=DriftSeverity.HIGH,
        cosine_distance=0.42,
        affected_columns=["col_a"],
        drift_vector={},
    )


@pytest.mark.asyncio
async def test_llm_diagnosis_does_not_block_event_loop(monkeypatch):
    """While `_llm_diagnosis` waits on the (slow) synchronous LLM call, a
    concurrently scheduled coroutine must keep making progress. If the call
    were made inline instead of via asyncio.to_thread, the event loop would
    stall for the full delay and the counter below would not advance until
    after `_llm_diagnosis` returns."""
    delay = 0.3
    blocking_llm = _BlockingLLM(delay)
    monkeypatch.setattr(
        "shared.llm_provider.get_llm", lambda *a, **kw: blocking_llm
    )

    agent = DiagnosticReflectorAgent()
    drift = _make_drift()

    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.02)

    ticker_task = asyncio.create_task(ticker())
    start = time.monotonic()
    diagnosis = await agent._llm_diagnosis(drift, error_logs=[])
    elapsed = time.monotonic() - start
    stop = True
    await ticker_task

    assert diagnosis is not None
    assert diagnosis.root_cause == "Simulated LLM diagnosis"
    assert diagnosis.drift_event_id == "batch-1"
    assert elapsed >= delay

    # The ticker should have advanced multiple times *during* the blocking
    # call, proving the event loop stayed free while the LLM call ran off
    # -thread. A blocking (non-offloaded) call would starve it to ~0-1 ticks.
    assert ticks >= 5, (
        f"event loop appears blocked during LLM call: only {ticks} ticks "
        f"in {elapsed:.3f}s"
    )


@pytest.mark.asyncio
async def test_llm_diagnosis_offloads_via_to_thread(monkeypatch):
    """Direct assertion on the offload mechanism itself: `_llm_diagnosis`
    must route the sync `generate_json` call through `asyncio.to_thread`."""
    calls = []
    real_to_thread = asyncio.to_thread

    async def spying_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    fast_llm = _BlockingLLM(delay_seconds=0.0)
    monkeypatch.setattr("shared.llm_provider.get_llm", lambda *a, **kw: fast_llm)
    monkeypatch.setattr("uasr.reflector_agent.asyncio.to_thread", spying_to_thread)

    agent = DiagnosticReflectorAgent()
    diagnosis = await agent._llm_diagnosis(_make_drift(), error_logs=[])

    assert diagnosis is not None
    assert calls == [fast_llm.generate_json]
