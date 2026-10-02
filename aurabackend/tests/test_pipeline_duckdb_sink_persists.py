"""BUG-279: the DuckDB sink created its table on the run's own in-memory connection,
which is closed when the run ends -- the run reported success, named an output table,
and kept nothing."""
from __future__ import annotations

import os

import duckdb
import pytest

from pipeline import engine as engine_module
from pipeline.engine import DUCKDB_SINK_FILE, PipelineEngine
from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    SinkType,
    SourceType,
)
from shared.storage.base import tenant_slug


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(engine_module, "OUTPUT_DIR", str(tmp_path / "processed"))
    from shared.storage import reset_storage_backend

    reset_storage_backend()
    yield
    reset_storage_backend()


async def _run(tenant: str = "acme", table: str = "clean_orders", if_exists: str = "replace",
               rows: bytes = b"id,v\n1,10\n2,20\n"):
    from shared.storage import get_storage_backend

    get_storage_backend().write(tenant, "orders.csv", rows)
    pipeline = Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="orders.csv"),
        steps=[],
        sink=PipelineSink(type=SinkType.DUCKDB, table=table, if_exists=if_exists),
    )
    return await PipelineEngine().execute(pipeline, tenant=tenant)


def _saved(tmp_path, tenant: str, table: str):
    path = os.path.join(str(tmp_path / "processed"), tenant_slug(tenant), DUCKDB_SINK_FILE)
    assert os.path.exists(path), "the DuckDB sink kept nothing on disk"
    con = duckdb.connect(path, read_only=True)
    try:
        return con.execute(f'SELECT id, v FROM "{table}" ORDER BY id').fetchall()
    finally:
        con.close()


@pytest.mark.asyncio
async def test_duckdb_sink_table_outlives_the_run(tmp_path):
    run = await _run()

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.output_table == "clean_orders"
    assert run.output_file == DUCKDB_SINK_FILE
    assert _saved(tmp_path, "acme", "clean_orders") == [(1, 10), (2, 20)]


@pytest.mark.asyncio
async def test_replace_overwrites_and_append_adds(tmp_path):
    await _run()
    await _run(rows=b"id,v\n3,30\n")
    assert _saved(tmp_path, "acme", "clean_orders") == [(3, 30)]

    run = await _run(rows=b"id,v\n4,40\n", if_exists="append")
    assert run.status == PipelineStatus.SUCCESS, run.error
    assert _saved(tmp_path, "acme", "clean_orders") == [(3, 30), (4, 40)]


@pytest.mark.asyncio
async def test_if_exists_fail_does_not_touch_an_existing_table(tmp_path):
    await _run()

    run = await _run(rows=b"id,v\n9,90\n", if_exists="fail")

    assert run.status == PipelineStatus.FAILED
    assert _saved(tmp_path, "acme", "clean_orders") == [(1, 10), (2, 20)]


@pytest.mark.asyncio
async def test_each_tenant_has_its_own_database(tmp_path):
    await _run(tenant="acme")
    await _run(tenant="globex", rows=b"id,v\n7,70\n")

    assert _saved(tmp_path, "acme", "clean_orders") == [(1, 10), (2, 20)]
    assert _saved(tmp_path, "globex", "clean_orders") == [(7, 70)]
