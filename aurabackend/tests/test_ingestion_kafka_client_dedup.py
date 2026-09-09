"""
Regression test for BUG-029 item 2: `ResilientKafkaProducer` had no
application-level idempotency/dedup key check before publish. The
`enable_idempotence=True` comment overstated what it actually guarantees --
that flag only dedups aiokafka's OWN internal retries of a single
`send_and_wait` call, not two independent `publish_with_retry` calls
carrying the same batch_id (e.g. a client re-POSTing after a timed-out
response). `ingestion_service` doesn't start in this deployment, so this is
the same "currently unreachable service" mitigation as BUG-010 -- low
priority, but a real fix rather than dead-and-forgotten now that it's
picked back up.

No local Kafka broker exists in this test environment (a genuine Tier B
external dependency per testing.md), so a fake producer implementing the
same async start/stop/send_and_wait surface stands in for AIOKafkaProducer
-- matching the pattern already used in
test_uasr_mapek_kafka_bootstrap_retry.py's `_FlakyThenHealthyConsumer`,
not a MagicMock stub.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ingestion_service.kafka_client as kafka_client_module  # noqa: E402
from ingestion_service.kafka_client import ResilientKafkaProducer  # noqa: E402


class _FakeProducer:
    def __init__(self, *args, **kwargs):
        self.sent: list[tuple[str, dict, str | None]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_and_wait(self, topic, value=None, key=None):
        self.sent.append((topic, value, key))


@pytest.fixture
def producer(monkeypatch):
    monkeypatch.setattr(kafka_client_module, "AIOKafkaProducer", _FakeProducer)
    return ResilientKafkaProducer(bootstrap_servers="fake:9092")


@pytest.mark.asyncio
async def test_duplicate_dedup_key_is_skipped_entirely(producer):
    await producer.publish_with_retry("t", {"a": 1}, dedup_key="batch-1")
    await producer.publish_with_retry("t", {"a": 1}, dedup_key="batch-1")

    assert len(producer.producer.sent) == 1, (
        "a second publish_with_retry call with the same dedup_key must not "
        "reach the broker at all -- this is the exact gap enable_idempotence "
        "does not cover"
    )


@pytest.mark.asyncio
async def test_different_dedup_keys_both_publish(producer):
    await producer.publish_with_retry("t", {"a": 1}, dedup_key="batch-1")
    await producer.publish_with_retry("t", {"a": 2}, dedup_key="batch-2")

    assert len(producer.producer.sent) == 2


@pytest.mark.asyncio
async def test_no_dedup_key_never_skips(producer):
    """dedup_key is opt-in -- callers that don't pass one (or DLQ routing,
    which never has a natural batch_id) must see unchanged behavior."""
    await producer.publish_with_retry("t", {"a": 1})
    await producer.publish_with_retry("t", {"a": 1})

    assert len(producer.producer.sent) == 2


@pytest.mark.asyncio
async def test_dedup_window_evicts_oldest_when_full(producer, monkeypatch):
    monkeypatch.setattr(kafka_client_module, "_DEDUP_WINDOW_SIZE", 2)

    await producer.publish_with_retry("t", {}, dedup_key="k1")
    await producer.publish_with_retry("t", {}, dedup_key="k2")
    await producer.publish_with_retry("t", {}, dedup_key="k3")  # evicts k1

    # k1 was evicted, so it can publish again.
    await producer.publish_with_retry("t", {}, dedup_key="k1")
    # k3 is still in the window, so this is skipped.
    await producer.publish_with_retry("t", {}, dedup_key="k3")

    assert len(producer.producer.sent) == 4  # k1, k2, k3, k1-again (not k3-again)
