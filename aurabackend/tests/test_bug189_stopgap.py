"""BUG-189 stopgap: a pipeline's DuckDB *query* source ran before the engine locked
its connection down, so any signed-up user could read files the gateway can read
(its environment, keys, every tenant's databases) through /pipeline/execute.
Query sources are refused until they run on a locked connection of their own;
table sources keep working."""
from __future__ import annotations

import duckdb
import pytest

from pipeline.engine import PipelineEngine
from pipeline.models import (
    JoinSource,
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)

SECRET = "AURA_TEST_SECRET_DO_NOT_LEAK_189"


def _isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()


def _secret_file(tmp_path):
    outside = tmp_path / "outside_secret.csv"
    outside.write_text(f"secret\n{SECRET}\n", encoding="utf-8")
    return outside.as_posix()


def _assert_refused(run):
    assert run.status == PipelineStatus.FAILED
    assert "BUG-189" in (run.error or "")
    assert SECRET not in str(run.model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "SELECT * FROM read_text('{path}')",
    "SELECT * FROM read_csv_auto('{path}')",
    "SELECT * FROM '{path}'",
])
async def test_a_query_source_cannot_read_a_server_file(tmp_path, monkeypatch, query):
    _isolate_storage(tmp_path, monkeypatch)
    pipeline = Pipeline(
        name="bug189",
        source=PipelineSource(type=SourceType.DUCKDB, query=query.format(path=_secret_file(tmp_path))),
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    _assert_refused(await PipelineEngine().execute(pipeline, preview_only=True, tenant="tenant_a"))


@pytest.mark.asyncio
async def test_a_join_query_source_cannot_read_a_server_file(tmp_path, monkeypatch):
    """Join sources load through the same unlocked path as the main source."""
    _isolate_storage(tmp_path, monkeypatch)
    from shared.storage import get_storage_backend
    get_storage_backend().write("tenant_a", "left.csv", b"id,v\n1,a\n")
    pipeline = Pipeline(
        name="bug189-join",
        source=PipelineSource(type=SourceType.FILE, file_name="left.csv"),
        steps=[ProcessingStep(type=StepType.JOIN, config={"on": "id"}, join_source=JoinSource(
            type=SourceType.DUCKDB, query=f"SELECT 1 AS id, * FROM read_text('{_secret_file(tmp_path)}')"))],
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    _assert_refused(await PipelineEngine().execute(pipeline, preview_only=True, tenant="tenant_a"))


@pytest.mark.asyncio
async def test_a_table_source_on_the_tenants_own_duckdb_file_still_works(tmp_path, monkeypatch):
    _isolate_storage(tmp_path, monkeypatch)
    from shared.storage import get_storage_backend
    scratch = tmp_path / "scratch.duckdb"
    con = duckdb.connect(str(scratch))
    con.execute("CREATE TABLE regions AS SELECT * FROM (VALUES (1, 'North'), (2, 'South')) t(id, name)")
    con.close()
    get_storage_backend().write("tenant_a", "warehouse.duckdb", scratch.read_bytes())
    pipeline = Pipeline(
        name="bug189-table",
        source=PipelineSource(type=SourceType.DUCKDB, table="regions", connection={"database": "warehouse.duckdb"}),
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    run = await PipelineEngine().execute(pipeline, preview_only=True, tenant="tenant_a")
    assert run.status == PipelineStatus.SUCCESS, run.error
    assert {r["name"] for r in run.preview_data} == {"North", "South"}
