"""BUG-288: a batch pipeline run had no step, time, memory or concurrency bound on the
one worker every tenant shares."""
from __future__ import annotations

import asyncio

import pytest

from pipeline import engine as engine_module
from pipeline.engine import PipelineEngine
from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend

    reset_storage_backend()
    engine_module._active_runs.clear()
    yield
    engine_module._active_runs.clear()
    reset_storage_backend()


def _pipeline(steps) -> Pipeline:
    from shared.storage import get_storage_backend

    get_storage_backend().write("acme", "t.csv", b"id\n1\n2\n")
    return Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="t.csv"),
        steps=steps,
        sink=PipelineSink(type=SinkType.PREVIEW),
    )


async def _run(steps, tenant: str = "acme"):
    return await PipelineEngine().execute(_pipeline(steps), preview_only=True, tenant=tenant)


@pytest.mark.asyncio
async def test_too_many_steps_fails_the_run():
    steps = [ProcessingStep(type=StepType.LIMIT, config={"count": 10})
             for _ in range(engine_module.MAX_PIPELINE_STEPS + 1)]

    run = await _run(steps)

    assert run.status == PipelineStatus.FAILED
    assert "at most" in run.error


@pytest.mark.asyncio
async def test_a_runaway_transform_is_stopped(monkeypatch):
    monkeypatch.setattr(engine_module, "TRANSFORM_TIMEOUT_SECONDS", 1.0)
    runaway = "SELECT count(*) AS n FROM range(200000) a, range(200000) b WHERE a.range + b.range = -1"

    run = await asyncio.wait_for(
        _run([ProcessingStep(type=StepType.CUSTOM_SQL, config={"expression": runaway})]), timeout=30)

    assert run.status == PipelineStatus.FAILED
    assert "were stopped" in run.error
    assert run.duration_ms < 15_000


@pytest.mark.asyncio
async def test_the_run_connection_has_a_memory_limit(monkeypatch):
    seen = {}
    real = engine_module.PipelineEngine._build_processing_sql

    def _spy(self, conn, *a, **k):
        seen["limit"] = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        return real(self, conn, *a, **k)

    monkeypatch.setattr(engine_module.PipelineEngine, "_build_processing_sql", _spy)

    run = await _run([])

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert seen["limit"] in ("512.0 MB", "488.2 MiB", "512.0 MiB")


@pytest.mark.asyncio
async def test_a_tenant_cannot_exceed_its_concurrent_runs_but_others_are_unaffected(monkeypatch):
    engine_module._active_runs["acme"] = engine_module.MAX_CONCURRENT_RUNS_PER_TENANT

    blocked = await _run([], tenant="acme")
    assert blocked.status == PipelineStatus.FAILED
    assert "already in progress" in blocked.error
    assert engine_module._active_runs["acme"] == engine_module.MAX_CONCURRENT_RUNS_PER_TENANT

    from shared.storage import get_storage_backend

    get_storage_backend().write("globex", "t.csv", b"id\n1\n")
    other = await PipelineEngine().execute(_pipeline([]), preview_only=True, tenant="globex")
    assert other.status == PipelineStatus.SUCCESS, other.error


@pytest.mark.asyncio
async def test_the_run_slot_is_released_after_success_and_after_failure():
    await _run([])
    await _run([ProcessingStep(type=StepType.SORT, config={"column": "no_such_column"})])

    assert engine_module._active_runs == {}
