"""BUG-414: a Kafka record that cannot be decoded must not wedge the MAPE-K worker."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import uasr.mapek_worker as mapek_worker
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker


@pytest.mark.asyncio
async def test_the_consumer_deserializer_never_raises(monkeypatch):
    captured = {}

    class _Consumer:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def start(self):
            raise ConnectionError("no broker")

        async def stop(self):
            pass

    monkeypatch.setattr(mapek_worker, "_AIOKAFKA_AVAILABLE", True)
    monkeypatch.setattr(mapek_worker, "AIOKafkaConsumer", _Consumer)
    with pytest.raises(ConnectionError):
        await MAPEKWorker(config=MAPEKConfig(source_id="s")).start()

    decode = captured["value_deserializer"]
    # aiokafka calls the deserializer before it advances past the record, so an
    # exception here is retried on the same offset forever.
    assert decode(None) is None  # tombstone
    assert decode(b"\xff\xfe") is None  # not UTF-8
    assert decode(b"not json") is None
    assert decode(b'{"id": 1}') == {"id": 1}


@pytest.mark.asyncio
async def test_rows_already_collected_survive_a_failed_poll():
    class _Consumer:
        def __init__(self):
            self.calls = 0

        async def getmany(self, timeout_ms, max_records):
            self.calls += 1
            if self.calls == 1:
                return {"tp": [SimpleNamespace(value={"id": 1}), SimpleNamespace(value=None)]}
            raise RuntimeError("broker went away")

    worker = MAPEKWorker(config=MAPEKConfig(source_id="s", batch_size=10, batch_window_seconds=5))
    worker._consumer = _Consumer()
    batch = await worker._monitor_pull_batch()
    assert batch is not None and batch.rows == [{"id": 1}]
