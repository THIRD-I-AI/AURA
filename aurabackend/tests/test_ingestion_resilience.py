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


# ── Task 2: lifespan + auditable publish failure ─────────────────────


def test_process_raw_batch_audits_publish_failure(dead_broker, monkeypatch):
    import ingestion_service.main as im

    events = []
    monkeypatch.setattr(im, "audit_event", lambda kind, payload: events.append((kind, payload)))
    monkeypatch.setattr(im.kafka_producer, "producer", None)
    payload = im.RawIngestionPayload(
        tenant_id="t1", batch_id="b1",
        entries=[{"tranId": "X1", "account": {"number": "4000"},
                  "amount": 10.0, "tranDate": "2026-06-01"}],
    )
    asyncio.run(im.process_raw_batch_async(payload, "NetSuite"))  # must NOT raise
    kinds = [k for k, _ in events]
    assert "ingestion_publish_failed" in kinds
    failed = dict(events)["ingestion_publish_failed"]
    assert failed["batch_id"] == "b1" and failed["entry_count"] == 1


def test_app_boots_with_broker_down(dead_broker, monkeypatch):
    from fastapi.testclient import TestClient

    import ingestion_service.main as im

    monkeypatch.setattr(im.kafka_producer, "producer", None)
    # on_event hooks must be gone — startup/shutdown live in the lifespan.
    assert im.app.router.on_startup == [] and im.app.router.on_shutdown == []
    with TestClient(im.app) as client:                      # runs the lifespan
        assert client.get("/health").status_code == 200
