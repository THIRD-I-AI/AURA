"""Audit round 6, pipelines area: BUG-457, BUG-458, BUG-459, BUG-460 (docs/BUG_REGISTRY.md)."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from pipeline.streaming import streaming_api as api
from pipeline.streaming.models import (
    RuntimeConfig,
    StreamPipelineStatus,
    StreamSink,
    StreamSinkType,
    StreamSource,
    StreamSourceType,
    WindowConfig,
)
from pipeline.streaming.sinks.kafka_sink import KafkaSink
from pipeline.streaming.sources.kafka_source import KafkaSource
from scheduler_service import executor as executor_mod
from scheduler_service.executor import JobExecutor
from scheduler_service.models import ScheduledJob
from shared import kafka_client
from shared.connector_policy import ConnectorPolicyError

# ── BUG-457: Kafka broker lists reach internal hosts ─────────────────
# No broker is available to the test lane; aiokafka is replaced by a fake that
# records every client built, so a refused config is proven never to connect.

@pytest.fixture
def fake_aiokafka(monkeypatch):
    monkeypatch.delenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", raising=False)
    built = []

    class _Client:
        def __init__(self, *args, **kwargs):
            built.append(kwargs.get("bootstrap_servers"))

        async def start(self):
            pass

        async def stop(self):
            pass

        async def getmany(self, timeout_ms=0, max_records=None):
            return {}

    mod = types.ModuleType("aiokafka")
    mod.AIOKafkaConsumer = _Client
    mod.AIOKafkaProducer = _Client
    errors = types.ModuleType("aiokafka.errors")
    errors.KafkaError = Exception
    monkeypatch.setitem(sys.modules, "aiokafka", mod)
    monkeypatch.setitem(sys.modules, "aiokafka.errors", errors)
    return built


INTERNAL_BROKERS = [
    "127.0.0.1:9092",
    "169.254.169.254:80",
    "8.8.8.8:9092,10.0.0.5:9092",  # one internal entry is enough to refuse
    "[::1]:9092",
    "redpanda.invalid:29092",  # an unresolvable name is refused, like the db connectors
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bootstrap", INTERNAL_BROKERS)
async def test_batch_kafka_source_refuses_internal_brokers(fake_aiokafka, bootstrap):
    with pytest.raises(ConnectorPolicyError):
        await kafka_client.consume_batch({"bootstrap_servers": bootstrap, "topic": "aura.uasr.events", "timeout_ms": 1})
    assert fake_aiokafka == []


@pytest.mark.asyncio
async def test_streaming_kafka_source_refuses_its_localhost_default(fake_aiokafka):
    with pytest.raises(ConnectorPolicyError):
        await KafkaSource({"topic": "aura.uasr.events"}).start()
    assert fake_aiokafka == []


@pytest.mark.asyncio
async def test_streaming_kafka_sink_refuses_internal_brokers(fake_aiokafka):
    with pytest.raises(ConnectorPolicyError):
        await KafkaSink({"topic": "aura.uasr.events", "bootstrap_servers": "10.1.2.3:9092"}).start()
    assert fake_aiokafka == []


@pytest.mark.asyncio
async def test_public_brokers_and_the_private_host_opt_in_still_connect(fake_aiokafka, monkeypatch):
    await kafka_client.consume_batch({"bootstrap_servers": "8.8.8.8:9092", "topic": "t", "timeout_ms": 1})
    assert fake_aiokafka == ["8.8.8.8:9092"]

    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")
    await KafkaSource({"topic": "t"}).start()
    assert fake_aiokafka[-1] == "localhost:9092"


# ── BUG-458: PUT stored nested fields as dicts ───────────────────────

@pytest.fixture
def clean_registry():
    saved = dict(api._pipelines), dict(api._engines)
    api._pipelines.clear()
    api._engines.clear()
    yield
    api._pipelines.clear()
    api._engines.clear()
    api._pipelines.update(saved[0])
    api._engines.update(saved[1])


@pytest.mark.asyncio
async def test_a_pipeline_updated_with_nested_fields_still_starts(clean_registry):
    user = {"org_id": "acme"}
    created = await api.create_pipeline(
        api.CreateStreamPipelineRequest(
            name="p", source=StreamSource(type=StreamSourceType.SIMULATED, config={})),
        user=user,
    )
    pid = created["id"]

    await api.update_pipeline(pid, api.UpdateStreamPipelineRequest(
        source=StreamSource(type=StreamSourceType.SIMULATED, config={"num_keys": 2}),
        runtime=RuntimeConfig(),
        window=WindowConfig(type="tumbling", size_seconds=60),
        sinks=[StreamSink(type=StreamSinkType.CONSOLE, config={})],
    ), user=user)

    pipe = api._pipelines[pid]
    assert isinstance(pipe.runtime, RuntimeConfig)
    assert isinstance(pipe.source, StreamSource)
    assert isinstance(pipe.window, WindowConfig)
    assert all(isinstance(s, StreamSink) for s in pipe.sinks)
    assert any(s.type == StreamSinkType.SSE for s in pipe.sinks), "the SSE sink create_pipeline guarantees was dropped"

    try:
        result = await api.start_pipeline(pid, user=user)
        assert result["status"] == StreamPipelineStatus.RUNNING.value
    finally:
        engine = api._engines.get(pid)
        if engine is not None:
            await engine.stop()


# ── BUG-459 / BUG-460: scheduler next-run calculation ────────────────

def _next_run(monkeypatch, now: datetime, schedule_type: str, config: dict) -> datetime:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(executor_mod, "datetime", _Frozen)
    job = ScheduledJob(
        id="job-1", name="j", connection_id="c", query="SELECT 1",
        schedule_type=schedule_type, schedule_config=config, is_active=True,
    )
    return JobExecutor.__new__(JobExecutor)._calculate_next_execution(job)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize("now, day, expected", [
    (_utc(2026, 3, 31, 9, 0, 5), 31, _utc(2026, 4, 30, 9, 0)),    # used to raise: April 31
    (_utc(2026, 1, 30, 9, 0, 5), 30, _utc(2026, 2, 28, 9, 0)),    # used to raise: Feb 30
    (_utc(2026, 2, 28, 9, 0, 5), 31, _utc(2026, 3, 31, 9, 0)),    # used to drift to Mar 28
    (_utc(2026, 12, 31, 9, 0, 5), 31, _utc(2027, 1, 31, 9, 0)),   # year rollover
    (_utc(2026, 4, 10, 8, 0), 31, _utc(2026, 4, 30, 9, 0)),       # still ahead this month
])
def test_monthly_next_run_clamps_to_the_target_month(monkeypatch, now, day, expected):
    assert _next_run(monkeypatch, now, "monthly", {"day": day, "hour": 9, "minute": 0}) == expected


@pytest.mark.parametrize("now, expected", [
    (_utc(2026, 10, 19, 8, 0), _utc(2026, 10, 19, 9, 0)),   # Monday, slot still ahead today
    (_utc(2026, 10, 19, 9, 0, 1), _utc(2026, 10, 26, 9, 0)),  # Monday, slot just passed
    (_utc(2026, 10, 21, 12, 0), _utc(2026, 10, 26, 9, 0)),  # Wednesday
])
def test_weekly_next_run_keeps_todays_slot_when_it_is_still_ahead(monkeypatch, now, expected):
    assert _next_run(monkeypatch, now, "weekly", {"day_of_week": 0, "hour": 9, "minute": 0}) == expected
