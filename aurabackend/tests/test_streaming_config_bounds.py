"""BUG-282: streaming pipeline config was unbounded -- ``num_keys`` sized a list built on
the event loop, buffers took any int, and a tenant could create and run any number of
pipelines on the one worker every tenant shares."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from pipeline.streaming import streaming_api as api
from pipeline.streaming.models import (
    RuntimeConfig,
    StreamPipeline,
    StreamPipelineStatus,
    StreamSink,
    StreamSinkType,
    StreamSource,
    StreamSourceType,
)
from pipeline.streaming.sources.simulated import SimulatedSource
from pipeline.streaming.sources.websocket_source import WebSocketSource


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(api._pipelines), dict(api._engines)
    api._pipelines.clear()
    api._engines.clear()
    yield
    api._pipelines.clear()
    api._engines.clear()
    api._pipelines.update(saved[0])
    api._engines.update(saved[1])


def _pipeline(tenant: str, status: StreamPipelineStatus = StreamPipelineStatus.DRAFT) -> StreamPipeline:
    p = StreamPipeline(
        name="p", tenant_id=tenant,
        source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
        sinks=[StreamSink(type=StreamSinkType.CONSOLE, config={})],
    )
    p.status = status
    api._pipelines[p.id] = p
    return p


def _create_request():
    return api.CreateStreamPipelineRequest(
        name="p", source=StreamSource(type=StreamSourceType.SIMULATED, config={}))


def test_simulated_source_key_count_and_rate_are_capped():
    source = SimulatedSource({"num_keys": 2_000_000_000, "events_per_second": 1e12})

    assert len(source._keys) == 10_000
    assert source.events_per_second == 10_000.0


def test_simulated_source_rejects_nonsense_low_values_by_clamping():
    source = SimulatedSource({"num_keys": -5, "events_per_second": 0})

    assert len(source._keys) == 1
    assert source.events_per_second == 0.1


def test_websocket_buffer_is_capped():
    assert WebSocketSource({"url": "wss://8.8.8.8/x", "max_buffer": 10**12})._max_buffer == 100_000


@pytest.mark.parametrize("value", [0, -1, 10**9])
def test_backpressure_buffer_out_of_range_is_rejected(value):
    with pytest.raises(ValidationError):
        RuntimeConfig(backpressure_buffer=value)


@pytest.mark.asyncio
async def test_a_tenant_cannot_create_more_than_the_pipeline_limit():
    for _ in range(api.MAX_PIPELINES_PER_TENANT):
        _pipeline("acme")

    with pytest.raises(HTTPException) as exc:
        await api.create_pipeline(_create_request(), user={"org_id": "acme"})
    assert exc.value.status_code == 409

    created = await api.create_pipeline(_create_request(), user={"org_id": "globex"})
    assert created["tenant_id"] == "globex", "another tenant's pipelines must not count against this one"


@pytest.mark.asyncio
async def test_a_tenant_cannot_run_more_than_the_running_limit():
    for _ in range(api.MAX_RUNNING_PER_TENANT):
        _pipeline("acme", StreamPipelineStatus.RUNNING)
    extra = _pipeline("acme")

    with pytest.raises(HTTPException) as exc:
        await api.start_pipeline(extra.id, user={"org_id": "acme"})

    assert exc.value.status_code == 409
    assert extra.id not in api._engines
    assert extra.status == StreamPipelineStatus.DRAFT


@pytest.mark.asyncio
async def test_another_tenants_running_pipelines_do_not_block_a_start():
    for _ in range(api.MAX_RUNNING_PER_TENANT):
        _pipeline("globex", StreamPipelineStatus.RUNNING)
    mine = _pipeline("acme")

    try:
        result = await api.start_pipeline(mine.id, user={"org_id": "acme"})
        assert result["status"] == StreamPipelineStatus.RUNNING.value
    finally:
        await api._engines[mine.id].stop()
