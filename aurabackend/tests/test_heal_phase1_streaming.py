"""BUG-569: the streaming engine swallowed source and sink errors, so a broken
streaming pipeline kept reporting RUNNING (docs/BUG_REGISTRY.md).

A source read failure killed the ingest loop with only a log line, a sink write
failure was only logged, and KafkaSource.read_batch returned [] on a read error,
which the engine cannot tell apart from "no data yet". These tests drive a real
StreamingEngine with tiny in-test sources and sinks (no broker, no network) and
check that errors are recorded, surface as DEGRADED / FAILED plus an error event
the webhook dispatcher maps to ``pipeline.failed``, and that a transient error
followed by success returns the pipeline to RUNNING.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, List

import pytest

from pipeline.streaming import streaming_api as api
from pipeline.streaming import streaming_engine as engine_mod
from pipeline.streaming.models import (
    StreamEvent,
    StreamPipeline,
    StreamPipelineStatus,
    StreamSink,
    StreamSinkType,
    StreamSource,
    StreamSourceType,
    WindowConfig,
    WindowType,
)
from pipeline.streaming.sinks.base import BaseSink
from pipeline.streaming.sources.base import BaseSource
from pipeline.streaming.streaming_engine import StreamingEngine
from shared.streaming_manager import streaming_manager
from shared.webhook_dispatcher import _classify


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # checkpoints land in a throwaway dir


_started: List[StreamingEngine] = []


@pytest.fixture(autouse=True)
async def _stop_engines():
    yield
    while _started:  # never leave loop tasks running into the next test
        await _started.pop().stop()


class ScriptedSource(BaseSource):
    """Each read pops the next step: an Exception is raised, a list is returned.
    Once the script runs out every read returns []. Records the pipeline status
    seen at each read so intermediate states can be asserted without racing."""

    def __init__(self, steps: List[Any]):
        super().__init__({})
        self.steps = list(steps)
        self.reads = 0
        self.statuses: List[StreamPipelineStatus] = []
        self.stopped = False
        self.engine: Any = None

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self.stopped = True
        self._running = False

    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        self.reads += 1
        if self.engine is not None:
            self.statuses.append(self.engine.pipeline.status)
        step = self.steps.pop(0) if self.steps else []
        if isinstance(step, Exception):
            raise step
        return step


class FailingSink(BaseSink):
    def __init__(self, fail_times: int):
        super().__init__({})
        self.fail_times = fail_times
        self.calls = 0
        self.written: List[Any] = []
        self.stopped = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self.stopped = True

    async def emit_window(self, window, pipeline_id: str) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("sink credentials expired: secret-dsn-detail")
        self.written.append(window)


def _pipeline(tenant: str = "tenant-569") -> StreamPipeline:
    return StreamPipeline(
        name="bug-569",
        source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
        window=WindowConfig(type=WindowType.TUMBLING, size_seconds=5),
        sinks=[StreamSink(type=StreamSinkType.CONSOLE)],
        watermark_delay_seconds=0,
        checkpoint_interval_seconds=3600,
        tenant_id=tenant,
    )


def _engine(monkeypatch, source: BaseSource, sink: BaseSink | None = None) -> StreamingEngine:
    monkeypatch.setattr(engine_mod, "_create_source", lambda _p: source)
    if sink is not None:
        monkeypatch.setattr(engine_mod, "_create_sink", lambda _d, _p: sink)
    eng = StreamingEngine(_pipeline(), tick_interval=0.01)
    eng._max_consecutive_errors = 3
    _started.append(eng)
    if isinstance(source, ScriptedSource):
        source.engine = eng
    return eng


async def _until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def _drain(queue: asyncio.Queue) -> List[Any]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def _windows_that_close() -> List[StreamEvent]:
    # The second event pushes the watermark far past the first event's window,
    # so that window fires and reaches the sinks on this batch.
    return [
        StreamEvent(timestamp=100.0, key="k", data={"v": 1}),
        StreamEvent(timestamp=10_000.0, key="k", data={"v": 2}),
    ]


# ── Source read errors ───────────────────────────────────────────────

async def test_source_read_error_degrades_then_fails_and_releases(monkeypatch):
    sub_id, queue = streaming_manager.subscribe("pipeline:*")
    try:
        boom = RuntimeError("token expired for user=admin password=hunter2")
        src = ScriptedSource([boom, boom, boom, boom, boom])
        eng = _engine(monkeypatch, src)
        await eng.start()
        await _until(lambda: eng.pipeline.status == StreamPipelineStatus.FAILED)

        # Read 1 saw RUNNING; reads 2 and 3 saw DEGRADED; read 3 hit the limit.
        assert src.statuses[:3] == [
            StreamPipelineStatus.RUNNING,
            StreamPipelineStatus.DEGRADED,
            StreamPipelineStatus.DEGRADED,
        ]
        assert src.reads == 3  # nothing reads the source after it is FAILED
        errors = eng.metrics.errors
        assert len(errors) == 3 and all(e.startswith("source:") for e in errors)
        assert not any("hunter2" in e for e in errors)  # BUG-127 still holds
        assert eng.metrics.status == StreamPipelineStatus.FAILED

        # FAILED releases the source and sinks on its own, as a run-loop failure does.
        await _until(lambda: src.stopped)
        await _until(lambda: eng._task.done() and eng._ingest_task.done())

        mine = [e for e in _drain(queue) if e.topic == f"pipeline:{eng.pipeline.id}"]
        # One event per status transition, not one per failed read.
        assert [e.payload["code"] for e in mine] == ["STREAM_DEGRADED", "STREAM_FAILED"]
        assert all(e.event_type == "error" and _classify(e) == "pipeline.failed" for e in mine)
        assert all(e.workspace_id == "tenant-569" for e in mine)
        assert all("hunter2" not in e.payload["error"] for e in mine)
    finally:
        streaming_manager.unsubscribe(sub_id)


async def test_transient_source_error_then_success_returns_to_running(monkeypatch):
    boom = ConnectionError("broker unreachable")
    src = ScriptedSource([boom, boom, [], []])
    eng = _engine(monkeypatch, src)
    await eng.start()
    try:
        await _until(lambda: src.reads >= 5)
        assert src.statuses[1:3] == [StreamPipelineStatus.DEGRADED, StreamPipelineStatus.DEGRADED]
        assert src.statuses[3] == StreamPipelineStatus.RUNNING  # read 3 succeeded
        assert eng.pipeline.status == StreamPipelineStatus.RUNNING
        assert len(eng.metrics.errors) == 2  # the history is kept after recovery
    finally:
        await eng.stop()
    assert eng.pipeline.status == StreamPipelineStatus.STOPPED


async def test_stop_releases_a_degraded_pipeline(monkeypatch):
    src = ScriptedSource([RuntimeError("x")] * 1000)
    eng = _engine(monkeypatch, src)
    eng._max_consecutive_errors = 1000
    await eng.start()
    await _until(lambda: eng.pipeline.status == StreamPipelineStatus.DEGRADED)
    await eng.stop()
    assert eng.pipeline.status == StreamPipelineStatus.STOPPED
    assert src.stopped


async def test_resume_keeps_an_unrecovered_pipeline_degraded(monkeypatch):
    src = ScriptedSource([RuntimeError("x")] * 1000)
    eng = _engine(monkeypatch, src)
    eng._max_consecutive_errors = 1000
    await eng.start()
    try:
        await _until(lambda: eng.pipeline.status == StreamPipelineStatus.DEGRADED)
        await eng.pause()
        await eng.resume()
        assert eng.pipeline.status == StreamPipelineStatus.DEGRADED
    finally:
        await eng.stop()


# ── Sink write errors ────────────────────────────────────────────────

async def test_sink_write_error_degrades_and_recovers_on_next_success(monkeypatch):
    sub_id, queue = streaming_manager.subscribe("pipeline:*")
    try:
        sink = FailingSink(fail_times=1)
        src = ScriptedSource([_windows_that_close()])
        eng = _engine(monkeypatch, src, sink)
        await eng.start()
        try:
            await _until(lambda: sink.calls >= 1)
            await _until(lambda: eng.pipeline.status == StreamPipelineStatus.DEGRADED)
            assert eng.metrics.errors == ["sink[0]: Internal server error"]
            mine = [e for e in _drain(queue) if e.topic == f"pipeline:{eng.pipeline.id}"]
            assert [(e.payload["code"], _classify(e)) for e in mine] == [("STREAM_DEGRADED", "pipeline.failed")]
            assert "secret-dsn-detail" not in mine[0].payload["error"]

            # The next window this sink writes successfully clears the degradation.
            src.steps = [[StreamEvent(timestamp=20_000.0, key="k", data={"v": 3})]]
            await _until(lambda: sink.written)
            await _until(lambda: eng.pipeline.status == StreamPipelineStatus.RUNNING)
        finally:
            await eng.stop()
    finally:
        streaming_manager.unsubscribe(sub_id)


async def test_persistently_failing_sink_fails_the_pipeline(monkeypatch):
    sink = FailingSink(fail_times=10_000)
    src = ScriptedSource([
        [StreamEvent(timestamp=t, key="k", data={}) for t in (100.0, 200.0, 300.0, 400.0, 500.0)],
    ])
    eng = _engine(monkeypatch, src, sink)
    await eng.start()
    await _until(lambda: eng.pipeline.status == StreamPipelineStatus.FAILED)
    assert sink.calls == 3
    await _until(lambda: sink.stopped and src.stopped)


# ── Kafka source ─────────────────────────────────────────────────────

async def test_kafka_read_failure_raises_a_typed_error_instead_of_returning_empty():
    from pipeline.streaming.sources.base import StreamSourceError
    from pipeline.streaming.sources.kafka_source import KafkaSource

    class _BrokenConsumer:
        async def getmany(self, timeout_ms=0, max_records=None):
            raise RuntimeError("SASL authentication failed: password=hunter2")

    src = KafkaSource({"topic": "t"})
    src._consumer = _BrokenConsumer()
    src._running = True
    with pytest.raises(StreamSourceError) as info:
        await src.read_batch()
    assert "hunter2" not in info.value.message
    assert isinstance(info.value.__cause__, RuntimeError)


async def test_kafka_empty_poll_still_returns_empty():
    from pipeline.streaming.sources.kafka_source import KafkaSource

    class _IdleConsumer:
        async def getmany(self, timeout_ms=0, max_records=None):
            return {}

    src = KafkaSource({"topic": "t"})
    src._consumer = _IdleConsumer()
    src._running = True
    assert await src.read_batch() == []


# ── API lifecycle guards ─────────────────────────────────────────────

async def test_api_treats_degraded_as_live(monkeypatch):
    pipe = _pipeline(tenant=None)
    pipe.status = StreamPipelineStatus.DEGRADED
    monkeypatch.setitem(api._pipelines, pipe.id, pipe)
    from fastapi import HTTPException

    # A degraded engine still holds its tasks, source and sinks (BUG-408's reasoning).
    with pytest.raises(HTTPException) as deleted:
        await api.delete_pipeline(pipe.id, user=None)
    assert deleted.value.status_code == 409
    with pytest.raises(HTTPException) as started:
        await api.start_pipeline(pipe.id, user=None)
    assert started.value.status_code == 409
    assert api._tenant_running_count(None, excluding="other") >= 1
