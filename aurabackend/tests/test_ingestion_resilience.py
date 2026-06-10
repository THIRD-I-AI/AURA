"""S34c — ingestion hardening: Kafka-down boot survival + auditable failures."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingestion_service import kafka_client as kc


class _DeadProducer:
    """Stands in for AIOKafkaProducer with the broker down."""
    instances = 0

    def __init__(self, **kwargs):
        type(self).instances += 1

    async def start(self):
        raise ConnectionError("broker down")

    async def stop(self):
        pass


class _LiveProducer:
    def __init__(self, **kwargs):
        self.sent = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_and_wait(self, topic, value=None, key=None):
        self.sent.append((topic, value, key))


@pytest.fixture()
def dead_broker(monkeypatch):
    _DeadProducer.instances = 0
    monkeypatch.setattr(kc, "AIOKafkaProducer", _DeadProducer)


@pytest.fixture()
def live_broker(monkeypatch):
    monkeypatch.setattr(kc, "AIOKafkaProducer", _LiveProducer)


# ── Task 1: tolerant producer ────────────────────────────────────────


def test_start_swallows_broker_down(dead_broker):
    p = kc.ResilientKafkaProducer()
    asyncio.run(p.start())                    # must NOT raise
    assert p.producer is None


def test_publish_raises_kafka_unavailable_after_lazy_retry(dead_broker):
    p = kc.ResilientKafkaProducer()
    asyncio.run(p.start())                    # boot attempt (fails, swallowed)
    with pytest.raises(kc.KafkaUnavailableError):
        asyncio.run(p.publish_with_retry("t", {"x": 1}, partition_key="k"))
    assert _DeadProducer.instances == 2       # boot + one lazy retry


def test_publish_works_when_broker_up(live_broker):
    p = kc.ResilientKafkaProducer()
    asyncio.run(p.start())
    asyncio.run(p.publish_with_retry("t", {"x": 1}, partition_key="k"))
    assert p.producer.sent == [("t", {"x": 1}, "k")]
