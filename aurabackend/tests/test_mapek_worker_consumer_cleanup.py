"""
Regression test for the confirmed live leak: every failed Kafka bootstrap
attempt on the production box left an ``AIOKafkaConsumer`` object dangling
without a ``.stop()`` call, logging "Unclosed AIOKafkaConsumer" on every
single retry -- forever, since BUG-046's fix makes ``start()`` retry
indefinitely on a box with no Kafka broker (aurabackend/uasr/mapek_worker.py,
``MAPEKWorker.start()``).

Live evidence (docker logs aura-uasr_service-1, 2026-09-09, post BUG-046/047
deploy at commit 631a0f8):
    ERROR | asyncio | Unclosed AIOKafkaConsumer
    consumer: <aiokafka.consumer.consumer.AIOKafkaConsumer object at ...>
    ERROR | aiokafka | Unable connect to "localhost:9092": ...
    ERROR | uasr.service | MAPE-K worker failed to start (retrying in 60s): ...
repeating every 60s indefinitely -- a small resource leak (sockets, internal
tasks) on every retry cycle, on a 1GB box where that matters over uptime.

Root cause: ``AIOKafkaConsumer.start()`` can allocate internal
sockets/background tasks before the bootstrap handshake itself fails --
``start()`` assigned ``self._consumer`` before calling ``.start()`` on it, so
a failed attempt's consumer object was simply dropped on the floor when the
exception propagated to the caller's retry loop, and aiokafka's own
finalizer complains it was never ``.stop()``'d.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uasr.mapek_worker as mapek_worker  # noqa: E402
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker  # noqa: E402


class _FailsToStartConsumer:
    """Reproduces the live box's exact failure: start() raises after
    allocating internal state, so stop() must still be safe/expected to
    be called on cleanup."""

    def __init__(self, *args, **kwargs):
        self.stop_called = False

    async def start(self) -> None:
        raise ConnectionError('Unable connect to "localhost:9092": Connection refused')

    async def stop(self) -> None:
        self.stop_called = True


@pytest.mark.asyncio
async def test_failed_consumer_start_is_cleaned_up_via_stop(monkeypatch, tmp_path):
    created: list[_FailsToStartConsumer] = []

    def _factory(*args, **kwargs):
        c = _FailsToStartConsumer(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mapek_worker, "AIOKafkaConsumer", _factory)
    monkeypatch.setattr(mapek_worker, "_AIOKAFKA_AVAILABLE", True)

    cfg = MAPEKConfig(
        source_id="src",
        duckdb_path=str(tmp_path / "lake.duckdb"),
        parquet_dir=str(tmp_path / "parquet"),
    )
    worker = MAPEKWorker(cfg)

    with pytest.raises(ConnectionError):
        await worker.start()

    assert len(created) == 1, "expected exactly one consumer to be constructed"
    assert created[0].stop_called, (
        "a consumer whose start() failed was never stop()'d -- this is the "
        "exact leak reproduced live: 'Unclosed AIOKafkaConsumer' logged on "
        "every failed retry, forever, once BUG-046 made retries indefinite"
    )
    assert worker._consumer is None, (
        "a consumer that failed to start must not be left assigned to "
        "self._consumer -- a later stop() call would try to stop a "
        "half-initialized object"
    )


@pytest.mark.asyncio
async def test_worker_can_still_start_after_a_failed_attempt(monkeypatch, tmp_path):
    """Not just cleanup -- confirms a fresh MAPEKWorker (as
    service._mapek_worker_bootstrap constructs on every retry) still
    succeeds normally once the consumer itself is willing to connect."""

    class _OKConsumer:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    monkeypatch.setattr(mapek_worker, "AIOKafkaConsumer", _OKConsumer)
    monkeypatch.setattr(mapek_worker, "_AIOKAFKA_AVAILABLE", True)

    cfg = MAPEKConfig(
        source_id="src",
        duckdb_path=str(tmp_path / "lake2.duckdb"),
        parquet_dir=str(tmp_path / "parquet2"),
    )
    worker = MAPEKWorker(cfg)
    await worker.start()
    try:
        assert worker._consumer is not None
        assert worker._running is True
    finally:
        await worker.stop()
