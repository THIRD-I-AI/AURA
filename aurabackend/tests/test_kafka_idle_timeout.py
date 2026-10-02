"""BUG-312: consume_batch's idle timeout could never fire at the default settings, so a
pipeline reading an idle or drained Kafka topic never finished.

No Kafka broker is available to the base test lane; the consumer is replaced by a fake
with aiokafka's getmany() contract (returns an empty dict when its own timeout_ms
elapses with nothing to read)."""
from __future__ import annotations

import asyncio
import sys
import time
import types
from collections import namedtuple

import pytest

from shared import kafka_client

Msg = namedtuple("Msg", "value")


def _fake_aiokafka(batches):
    class FakeConsumer:
        def __init__(self, topic, **kwargs):
            self._batches = list(batches)
            self.polls = 0

        async def start(self):
            pass

        async def stop(self):
            pass

        async def getmany(self, timeout_ms=0, max_records=None):
            self.polls += 1
            if self._batches:
                return {"tp0": self._batches.pop(0)}
            await asyncio.sleep(timeout_ms / 1000.0)
            return {}

    mod = types.ModuleType("aiokafka")
    mod.AIOKafkaConsumer = FakeConsumer
    errors = types.ModuleType("aiokafka.errors")
    errors.KafkaError = Exception
    return mod, errors


async def _consume(monkeypatch, batches, **cfg):
    mod, errors = _fake_aiokafka(batches)
    monkeypatch.setitem(sys.modules, "aiokafka", mod)
    monkeypatch.setitem(sys.modules, "aiokafka.errors", errors)
    return await asyncio.wait_for(
        kafka_client.consume_batch({"bootstrap_servers": "b:9092", "topic": "t", **cfg}), timeout=20)


@pytest.mark.asyncio
async def test_an_empty_topic_stops_after_the_idle_timeout(monkeypatch):
    started = time.perf_counter()

    rows = await _consume(monkeypatch, [], timeout_ms=1500)

    assert rows == []
    assert 1.4 <= time.perf_counter() - started < 6


@pytest.mark.asyncio
async def test_a_drained_topic_returns_what_it_read(monkeypatch):
    batches = [[Msg(b'{"id": 1}'), Msg(b'{"id": 2}')], [Msg(b'{"id": 3}')]]

    rows = await _consume(monkeypatch, batches, timeout_ms=1200)

    assert [r["id"] for r in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_a_sub_second_idle_timeout_is_honoured(monkeypatch):
    started = time.perf_counter()

    await _consume(monkeypatch, [], timeout_ms=200)

    assert time.perf_counter() - started < 3
