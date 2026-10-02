"""BUG-284: a failed run's ``run.error`` was the raw ``str(exc)`` -- driver text with
internal hosts, absolute server paths -- and both the sync response and the SSE stream
return it to the caller."""
from __future__ import annotations

import duckdb
import pytest

from pipeline import engine as engine_module
from pipeline.engine import PipelineEngine, _client_error
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
    yield
    reset_storage_backend()


async def _run(steps, monkeypatch=None, load_error: BaseException | None = None):
    from shared.storage import get_storage_backend

    get_storage_backend().write("acme", "t.csv", b"id,amount\n1,10\n")
    if load_error is not None:
        async def _boom(self, *a, **k):
            raise load_error
        monkeypatch.setattr(engine_module.PipelineEngine, "_load_source", _boom)
    pipeline = Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="t.csv"),
        steps=steps,
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    return await PipelineEngine().execute(pipeline, preview_only=True, tenant="acme")


@pytest.mark.asyncio
async def test_an_unexpected_exception_is_not_echoed(monkeypatch):
    secret = "could not connect to db-internal-7.corp.local:5432 as svc_aura"
    run = await _run([], monkeypatch, load_error=OSError(secret))

    assert run.status == PipelineStatus.FAILED
    assert "db-internal-7" not in run.error and "svc_aura" not in run.error
    assert "server log" in run.error


@pytest.mark.asyncio
async def test_a_library_valueerror_subclass_is_not_echoed(monkeypatch):
    class DriverError(ValueError):
        pass

    run = await _run([], monkeypatch, load_error=DriverError("/opt/aura/secrets/pg.conf is unreadable"))

    assert "/opt/aura" not in run.error and "server log" in run.error


@pytest.mark.asyncio
async def test_the_engines_own_message_still_reaches_the_caller():
    run = await _run([ProcessingStep(type=StepType.LIMIT, config={"count": 1})])
    assert run.status == PipelineStatus.SUCCESS

    from shared.storage import get_storage_backend

    get_storage_backend().write("acme", "t.csv", b"id,amount\n1,10\n")
    pipeline = Pipeline(
        name="p", source=PipelineSource(type=SourceType.FILE, file_name="missing.csv"),
        steps=[], sink=PipelineSink(type=SinkType.PREVIEW),
    )
    run = await PipelineEngine().execute(pipeline, preview_only=True, tenant="acme")
    assert run.status == PipelineStatus.FAILED
    assert "missing.csv" in run.error


@pytest.mark.asyncio
async def test_a_sql_error_names_the_column_so_the_step_can_be_fixed():
    run = await _run([ProcessingStep(type=StepType.SORT, config={"column": "no_such_column"})])

    assert run.status == PipelineStatus.FAILED
    assert "no_such_column" in run.error


@pytest.mark.parametrize("path", [
    "C:\\Users\\svc\\aura\\data\\uploads\\acme\\t.csv",
    "/opt/aura/aurabackend/data/processed/acme/out.csv",
])
def test_server_paths_are_removed_from_a_duckdb_error(path):
    message = _client_error(duckdb.IOException(f'Cannot open file "{path}": Permission denied'))

    assert "aura" not in message and "<path>" in message
    assert "Permission denied" in message
