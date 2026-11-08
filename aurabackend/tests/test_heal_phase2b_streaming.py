"""ADR-002 phase 2b: the heal step of a streaming pipeline, per micro-batch.

A real StreamingEngine streams rows from the phase-2a benchmark's datasets and fault
injectors (tests/accuracy) through the real /uasr/heal and /uasr/baseline handlers,
behind the real JWTAuthMiddleware and reached over HTTP (httpx.ASGITransport, no
lifespan), as the pipeline's tenant. What reaches the window processor -- the input
of every sink -- is recorded and compared with the injector's ground truth:

* a clean stream flows through unchanged, with nothing quarantined;
* a repairable fault is repaired before any sink sees it;
* rows no rule can prove are quarantined to ``_aura_quarantine_<pipeline_id>`` and
  never emitted;
* with UASR unreachable nothing is emitted: the micro-batch is held, the pipeline
  goes DEGRADED then FAILED on BUG-569's thresholds, and every event read ends up in
  the quarantine table; a transient outage releases the held events exactly once;
* each tenant's stream is healed against its own baseline only.

Row accounting is checked throughout: every event the source produced is emitted or
quarantined, exactly once.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Optional

import duckdb
import httpx
import pytest
from fastapi import FastAPI, Request

import metadata_store.db as _metadata_db
from pipeline.streaming import path_confinement
from pipeline.streaming import streaming_engine as engine_mod
from pipeline.streaming.heal import QUARANTINE_FILE, MicroBatchHealer, source_id_for
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
from shared.middleware import JWTAuthMiddleware
from shared.streaming_manager import streaming_manager
from tests.accuracy.datasets import make_dataset, strict_equal
from tests.accuracy.faults import build_trial
from uasr import service
from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.db import get_session_factory, init_uasr_db

BATCH = 100


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    path = os.path.join(tempfile.gettempdir(), f"aura_heal2b_stream_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    yield
    leaked = _metadata_db._engine  # BUG-008: dispose before restoring, or pytest hangs at exit
    if leaked is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(leaked.dispose())
        finally:
            loop.close()
    _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
    try:
        os.remove(path)
    except OSError:
        pass


async def _no_llm(self, *a, **k):
    return None


@pytest.fixture(autouse=True)
async def _env(tmp_path, monkeypatch):
    await init_uasr_db()
    # No LLM in CI: an unproven fault's proposal path falls back to templates.
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    monkeypatch.chdir(tmp_path)  # checkpoints
    monkeypatch.setattr(path_confinement, "STREAMING_OUTPUT_ROOT", str(tmp_path / "streaming_output"))
    yield
    while _started:
        await _started.pop().stop()


def _uasr_app() -> FastAPI:
    """The real UASR heal/baseline handlers and tenancy behind the real JWT middleware,
    as tests/test_heal_phase1_monitor_livecheck.py builds it."""
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)

    @app.post("/uasr/baseline")
    async def baseline(request: Request):
        return await service.register_baseline(service.BaselineRequest(**await request.json()), request)

    @app.post("/uasr/heal")
    async def heal(request: Request):
        async with get_session_factory()() as db:
            return await service.heal_batch(service.IngestRequest(**await request.json()), request, db=db)

    return app


class FlakyTransport(httpx.AsyncBaseTransport):
    """Refuses the first ``failures`` connections, then reaches ``inner``: UASR down."""

    def __init__(self, inner: Optional[httpx.AsyncBaseTransport], failures: int):
        self.inner = inner
        self.failures = failures
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.inner is None or self.calls <= self.failures:
            raise httpx.ConnectError("connection refused", request=request)
        return await self.inner.handle_async_request(request)


class ListSource(BaseSource):
    """Hands out the scripted micro-batches, then nothing."""

    def __init__(self, chunks: List[List[StreamEvent]]):
        super().__init__({})
        self.chunks = list(chunks)
        self.produced = 0

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        if not self.chunks:
            return []
        chunk = self.chunks.pop(0)
        self.produced += len(chunk)
        return chunk


class RecordingSink(BaseSink):
    def __init__(self):
        super().__init__({})
        self.windows = 0

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def emit_window(self, window, pipeline_id: str) -> None:
        self.windows += 1


_started: List[StreamingEngine] = []


def _pipeline(tenant: Optional[str], pipeline_id: Optional[str] = None) -> StreamPipeline:
    kw: Dict[str, Any] = {"id": pipeline_id} if pipeline_id else {}
    return StreamPipeline(
        name="heal-2b",
        source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
        window=WindowConfig(type=WindowType.TUMBLING, size_seconds=5),
        sinks=[StreamSink(type=StreamSinkType.CONSOLE)],
        watermark_delay_seconds=0,
        checkpoint_interval_seconds=3600,
        tenant_id=tenant,
        **kw,
    )


def _events(rows: List[Dict[str, Any]], t0: float = 1_000.0) -> List[StreamEvent]:
    return [StreamEvent(timestamp=t0 + i, key="k", data=dict(r)) for i, r in enumerate(rows)]


def _chunks(events: List[StreamEvent]) -> List[List[StreamEvent]]:
    return [events[i:i + BATCH] for i in range(0, len(events), BATCH)]


class Run:
    def __init__(self, engine: StreamingEngine, source: ListSource, sink: RecordingSink):
        self.engine, self.source, self.sink = engine, source, sink
        self.emitted: List[StreamEvent] = []


async def _start(monkeypatch, pipeline: StreamPipeline, events: List[StreamEvent], *,
                 baseline_rows: int, transport: Optional[httpx.AsyncBaseTransport] = None,
                 max_errors: Optional[int] = None) -> Run:
    transport = transport or httpx.ASGITransport(app=_uasr_app())
    source, sink = ListSource(_chunks(events)), RecordingSink()
    monkeypatch.setattr(engine_mod, "_create_source", lambda _p: source)
    monkeypatch.setattr(engine_mod, "_create_sink", lambda _d, _p: sink)
    monkeypatch.setattr(engine_mod, "_create_healer", lambda p: MicroBatchHealer(p, transport=transport))
    eng = StreamingEngine(pipeline, batch_size=BATCH, tick_interval=0.01)
    eng._baseline_min_events = baseline_rows
    eng._baseline_max_wait = 60.0
    if max_errors is not None:
        eng._max_consecutive_errors = max_errors
    _started.append(eng)
    run = Run(eng, source, sink)
    await eng.start()
    real = eng._window_proc.process_event

    def spy(ev):  # the window processor's input is every sink's input
        run.emitted.append(ev)
        return real(ev)

    eng._window_proc.process_event = spy
    return run


async def _until(cond: Callable[[], bool], timeout: float = 60.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def _quarantine(pipeline: StreamPipeline) -> List[Dict[str, Any]]:
    path = os.path.join(path_confinement.tenant_output_dir(pipeline.tenant_id, pipeline.id), QUARANTINE_FILE)
    if not os.path.exists(path):
        return []
    con = duckdb.connect(path, read_only=True)
    try:
        cur = con.execute(f'SELECT event_id, reason, row_json, source_id FROM "_aura_quarantine_{pipeline.id}"')
        return [{"event_id": e, "reason": r, "row": json.loads(j), "source_id": s} for e, r, j, s in cur.fetchall()]
    finally:
        con.close()


def _accounted(run: Run, quarantined: List[Dict[str, Any]], sent: int) -> None:
    ids = [e.event_id for e in run.emitted] + [q["event_id"] for q in quarantined]
    assert len(ids) == sent == run.source.produced, (len(run.emitted), len(quarantined), sent)
    assert len(set(ids)) == sent, "an event was emitted or quarantined twice"


async def _drain(run: Run, sent: int) -> None:
    eng = run.engine
    await _until(lambda: eng.metrics.events_in == sent)
    await eng.stop()


# ── Clean data ───────────────────────────────────────────────────────────────

async def test_clean_stream_flows_through_unchanged(monkeypatch):
    ds = make_dataset(11, batch_rows=500)
    pipe = _pipeline("tenant_a")
    events = _events(ds.baseline + ds.truth)
    run = await _start(monkeypatch, pipe, events, baseline_rows=len(ds.baseline))
    await _until(lambda: run.engine.metrics.events_in == len(events))
    assert run.engine.pipeline.status == StreamPipelineStatus.RUNNING
    assert run.engine.metrics.errors == []
    await run.engine.stop()

    # The first rows registered the baseline, as this tenant's source.
    assert service._detector.has_baseline(service.scoped_source("tenant_a", source_id_for(pipe)))
    assert [e.event_id for e in run.emitted] == [e.event_id for e in events]  # order kept
    assert all(strict_equal(e.data, r) for e, r in zip(run.emitted, ds.baseline + ds.truth))
    assert _quarantine(pipe) == []
    assert run.engine.metrics.events_quarantined == 0 and run.engine.metrics.heal_repairs == 0


# ── Repaired before the sink ─────────────────────────────────────────────────

async def test_repairable_fault_in_micro_batches_is_repaired_before_the_sink(monkeypatch):
    trial = build_trial("rename", 3)
    pipe = _pipeline("tenant_a")
    base, faulty = _events(trial.dataset.baseline), _events(trial.sent, t0=50_000.0)
    run = await _start(monkeypatch, pipe, base + faulty, baseline_rows=len(base))
    await _drain(run, len(base) + len(faulty))

    quarantined = _quarantine(pipe)
    _accounted(run, quarantined, len(base) + len(faulty))
    by_id = {e.event_id: e for e in run.emitted}
    restored = 0
    for ev, want in zip(faulty, trial.expected):
        if ev.event_id in by_id:
            # Never a wrong value at the sink: what is emitted is the ground truth.
            assert want is not None and strict_equal(by_id[ev.event_id].data, want), trial.detail
            restored += 1
    assert restored == len(faulty), f"{trial.detail}: only {restored}/{len(faulty)} restored"
    assert run.engine.metrics.heal_repairs > 0


# ── Unprovable: quarantined, never emitted ───────────────────────────────────

async def test_unprovable_fault_is_quarantined_and_never_emitted(monkeypatch):
    sub_id, queue = streaming_manager.subscribe("pipeline:*")
    try:
        trial = build_trial("retype_lossy", 0)
        pipe = _pipeline("tenant_a")
        base, faulty = _events(trial.dataset.baseline), _events(trial.sent, t0=50_000.0)
        run = await _start(monkeypatch, pipe, base + faulty, baseline_rows=len(base))
        await _until(lambda: run.engine.metrics.events_in == len(base) + len(faulty))
        assert run.engine.pipeline.status == StreamPipelineStatus.DEGRADED
        await run.engine.stop()

        quarantined = _quarantine(pipe)
        _accounted(run, quarantined, len(base) + len(faulty))
        must_hold = {ev.event_id for ev, want in zip(faulty, trial.expected) if want is None}
        assert must_hold, trial.detail
        held = {q["event_id"] for q in quarantined}
        assert must_hold <= held, trial.detail
        assert not must_hold & {e.event_id for e in run.emitted}
        sent_by_id = {ev.event_id: ev.data for ev in faulty}
        for q in quarantined:  # held aside exactly as read, with the reason and the source
            assert strict_equal(q["row"], sent_by_id[q["event_id"]]) and q["reason"]
            assert q["source_id"] == source_id_for(pipe)
        for ev, want in zip(faulty, trial.expected):
            match = [e for e in run.emitted if e.event_id == ev.event_id]
            if match:
                assert want is not None and strict_equal(match[0].data, want)
        assert run.engine.metrics.events_quarantined == len(quarantined)
        mine = [e for e in _drain_queue(queue) if e.topic == f"pipeline:{pipe.id}"]
        assert [e.payload["code"] for e in mine] == ["STREAM_DEGRADED"]
    finally:
        streaming_manager.unsubscribe(sub_id)


def _drain_queue(queue: asyncio.Queue) -> List[Any]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# ── UASR unreachable: fail closed ────────────────────────────────────────────

async def test_uasr_down_holds_the_stream_then_fails_and_loses_nothing(monkeypatch):
    sub_id, queue = streaming_manager.subscribe("pipeline:*")
    try:
        ds = make_dataset(5, batch_rows=150)
        pipe = _pipeline("tenant_a")
        events = _events(ds.baseline[:200] + ds.truth)
        flaky = FlakyTransport(None, failures=10**9)
        run = await _start(monkeypatch, pipe, events, baseline_rows=200, transport=flaky)
        seen: List[StreamPipelineStatus] = []
        original_tick = run.engine._component_tick

        async def recording_tick(component, errors, successes):
            failed = await original_tick(component, errors, successes)
            if component == "heal":
                seen.append(run.engine.pipeline.status)
            return failed

        run.engine._component_tick = recording_tick
        await _until(lambda: run.engine.pipeline.status == StreamPipelineStatus.FAILED)
        await _until(lambda: run.engine._released)

        # BUG-569 thresholds, unchanged: DEGRADED on the first failed tick, FAILED on the 10th.
        assert seen[0] == StreamPipelineStatus.DEGRADED
        assert seen[-1] == StreamPipelineStatus.FAILED
        assert len(seen) == engine_mod._MAX_CONSECUTIVE_ERRORS
        assert flaky.calls == engine_mod._MAX_CONSECUTIVE_ERRORS
        assert run.emitted == [] and run.sink.windows == 0  # nothing unchecked reached a sink
        assert all(e.startswith("heal: UASR /uasr/baseline unreachable") for e in run.engine.metrics.errors)

        quarantined = _quarantine(pipe)
        _accounted(run, quarantined, len(events))
        assert all(q["reason"].startswith("not checked") for q in quarantined)
        mine = [e for e in _drain_queue(queue) if e.topic == f"pipeline:{pipe.id}"]
        assert [e.payload["code"] for e in mine] == ["STREAM_DEGRADED", "STREAM_FAILED"]
    finally:
        streaming_manager.unsubscribe(sub_id)


async def test_transient_uasr_outage_releases_held_events_exactly_once(monkeypatch):
    ds = make_dataset(7, batch_rows=300)
    pipe = _pipeline("tenant_a")
    base, rest = _events(ds.baseline), _events(ds.truth, t0=50_000.0)
    flaky = FlakyTransport(httpx.ASGITransport(app=_uasr_app()), failures=0)
    run = await _start(monkeypatch, pipe, base, baseline_rows=len(base), transport=flaky)
    await _until(lambda: run.engine.metrics.events_in == len(base))
    await _until(lambda: run.engine.pipeline.status == StreamPipelineStatus.RUNNING)

    flaky.failures = flaky.calls + 3  # the next three calls are refused
    run.source.chunks = _chunks(rest)
    await _until(lambda: run.engine.pipeline.status == StreamPipelineStatus.DEGRADED)
    assert run.engine.metrics.events_held > 0
    assert len(run.emitted) == len(base)  # held, not emitted
    await _until(lambda: run.engine.metrics.events_in == len(base) + len(rest))
    await _until(lambda: run.engine.pipeline.status == StreamPipelineStatus.RUNNING)  # 3 clean ticks
    await run.engine.stop()

    assert _quarantine(pipe) == []
    _accounted(run, [], len(base) + len(rest))
    assert all(strict_equal(e.data, r) for e, r in zip(run.emitted, ds.baseline + ds.truth))


# ── Tenant isolation ─────────────────────────────────────────────────────────

async def test_each_tenant_is_healed_against_its_own_baseline(monkeypatch):
    trial = build_trial("rename", 3)
    shared_id = f"spipe_{uuid.uuid4().hex[:8]}"  # the same pipeline id and source in both tenants

    # Tenant A registers the clean baseline.
    pipe_a = _pipeline("tenant_a", shared_id)
    base = _events(trial.dataset.baseline)
    run_a = await _start(monkeypatch, pipe_a, base, baseline_rows=len(base))
    await _drain(run_a, len(base))

    # Tenant B streams renamed rows under the same ids. They must not meet A's
    # baseline (they would be "repaired" into A's column names): B has no baseline,
    # so its first rows register B's own and pass unchanged.
    pipe_b = _pipeline("tenant_b", shared_id)
    faulty = _events(trial.sent)
    run_b = await _start(monkeypatch, pipe_b, faulty, baseline_rows=len(faulty))
    await _drain(run_b, len(faulty))
    assert all(strict_equal(e.data, r) for e, r in zip(run_b.emitted, trial.sent))
    assert len(run_b.emitted) == len(faulty) and run_b.engine.metrics.heal_repairs == 0

    sid = source_id_for(pipe_a)
    assert service._detector.has_baseline(service.scoped_source("tenant_a", sid))
    assert service._detector.has_baseline(service.scoped_source("tenant_b", sid))
    # Each tenant's quarantine lives in its own directory.
    assert path_confinement.tenant_output_dir("tenant_a", shared_id) != \
        path_confinement.tenant_output_dir("tenant_b", shared_id)


async def test_a_pipeline_without_a_tenant_is_held_not_sent_as_a_shared_identity(monkeypatch):
    # BUG-562: no verified tenant -> no credentials, so the JWT UASR answers 401 and
    # the stream is held (fail closed) instead of being healed in some shared namespace.
    ds = make_dataset(13, batch_rows=60)
    pipe = _pipeline(None)
    events = _events(ds.truth)
    run = await _start(monkeypatch, pipe, events, baseline_rows=len(events), max_errors=3)
    await _until(lambda: run.engine.pipeline.status == StreamPipelineStatus.FAILED)
    await _until(lambda: run.engine._released)
    assert run.emitted == []
    assert run.engine.metrics.errors[-1] == "heal: UASR /uasr/baseline returned HTTP 401"
    _accounted(run, _quarantine(pipe), len(events))


async def test_stop_sweep_does_not_quarantine_an_already_written_batch_twice():
    # A stop can cancel the run loop while a micro-batch's quarantine write runs in its
    # thread; that batch is still held, so the stop sweep sees it again. It must land
    # in the table once (ADR-002: every row accounted for exactly once).
    from pipeline.streaming.heal import QuarantineStore

    pipe = _pipeline("tenant_a")
    store = QuarantineStore(pipe, run_id=f"{pipe.id}:r1")
    events = _events([{"a": i} for i in range(5)])
    assert await store.write("b1", [(ev, "unprovable") for ev in events[:3]]) == 3
    swept = await store.write("unchecked", [(ev, "not checked") for ev in events], skip_written=True)
    assert swept == 2
    rows = _quarantine(pipe)
    assert sorted(r["event_id"] for r in rows) == sorted(ev.event_id for ev in events)
    # Another run of the same pipeline is not this run's write: its events are written.
    other = QuarantineStore(pipe, run_id=f"{pipe.id}:r2")
    assert await other.write("unchecked", [(events[0], "not checked")], skip_written=True) == 1
