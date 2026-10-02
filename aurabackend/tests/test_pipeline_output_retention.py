"""BUG-289: auto-named pipeline output files and a deleted streaming pipeline's
checkpoint directory were never removed, so the shared volume only ever grew."""
from __future__ import annotations

import os

import pytest

from pipeline import engine as engine_module
from pipeline.engine import PipelineEngine
from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    SinkType,
    SourceType,
)
from pipeline.streaming import state_manager
from pipeline.streaming import streaming_api as api
from pipeline.streaming.models import (
    StreamPipeline,
    StreamSink,
    StreamSinkType,
    StreamSource,
    StreamSourceType,
)
from shared.storage.base import tenant_slug


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(engine_module, "OUTPUT_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(engine_module, "MAX_AUTO_NAMED_OUTPUTS", 3)
    monkeypatch.setattr(state_manager, "CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    from shared.storage import reset_storage_backend

    reset_storage_backend()
    yield
    reset_storage_backend()


async def _run(tenant: str, file_name: str | None = None):
    from shared.storage import get_storage_backend

    get_storage_backend().write(tenant, "t.csv", b"id\n1\n")
    pipeline = Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="t.csv"),
        steps=[],
        sink=PipelineSink(type=SinkType.FILE, format="csv", file_name=file_name),
    )
    run = await PipelineEngine().execute(pipeline, tenant=tenant)
    assert run.status == PipelineStatus.SUCCESS, run.error
    return run.output_file


def _outputs(tmp_path, tenant: str) -> set:
    return set(os.listdir(os.path.join(str(tmp_path / "processed"), tenant_slug(tenant))))


@pytest.mark.asyncio
async def test_only_the_newest_auto_named_outputs_are_kept(tmp_path):
    names = []
    for i in range(5):
        name = await _run("acme")
        names.append(name)
        path = os.path.join(str(tmp_path / "processed"), tenant_slug("acme"), name)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))

    assert _outputs(tmp_path, "acme") == set(names[-3:]) | {names[-1]}
    assert len(_outputs(tmp_path, "acme")) <= 3
    assert names[-1] in _outputs(tmp_path, "acme"), "the run that just finished must keep its file"


@pytest.mark.asyncio
async def test_files_the_caller_named_are_never_pruned(tmp_path):
    await _run("acme", file_name="quarterly_report")
    for _ in range(5):
        await _run("acme")

    assert "quarterly_report.csv" in _outputs(tmp_path, "acme")


@pytest.mark.asyncio
async def test_pruning_does_not_touch_another_tenant(tmp_path):
    theirs = await _run("globex")
    for _ in range(5):
        await _run("acme")

    assert _outputs(tmp_path, "globex") == {theirs}


@pytest.mark.asyncio
async def test_deleting_a_streaming_pipeline_removes_its_checkpoints(tmp_path):
    pipe = StreamPipeline(
        name="p", tenant_id="acme",
        source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
        sinks=[StreamSink(type=StreamSinkType.CONSOLE, config={})],
    )
    api._pipelines[pipe.id] = pipe
    mgr = state_manager.StateManager(pipe.id)
    with open(os.path.join(mgr.checkpoint_dir, "checkpoint_1.json"), "w") as f:
        f.write("{}")
    other_dir = state_manager.StateManager("spipe_other").checkpoint_dir

    try:
        await api.delete_pipeline(pipe.id, user={"org_id": "acme"})
    finally:
        api._pipelines.pop(pipe.id, None)

    assert not os.path.exists(mgr.checkpoint_dir)
    assert os.path.isdir(other_dir), "another pipeline's checkpoints must survive"
