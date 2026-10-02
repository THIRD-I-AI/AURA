"""BUG-276: pipeline step SQL could read any local file.

The expression guard is a blocklist and missed two DuckDB replacement-scan forms -- a
double-quoted path after FROM, and a string literal after a comma -- so a CUSTOM_SQL or
ADD_COLUMN step could read another tenant's upload. The run's connection is now cut
off from the filesystem before any step SQL runs."""
from __future__ import annotations

import os

import pytest

from pipeline.engine import OUTPUT_DIR, PipelineEngine
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
from shared.storage.base import tenant_slug

SECRET = "TOP-SECRET-PAYROLL"


def _isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()


def _victim_file(tmp_path) -> str:
    """Another tenant's file, addressed the way an attacker would: by path."""
    from shared.storage import get_storage_backend

    get_storage_backend().write("victim", "payroll.csv", f"name,salary\n{SECRET},999\n".encode())
    path = os.path.join(str(tmp_path), tenant_slug("victim"), "payroll.csv")
    assert os.path.exists(path), "fixture: the victim's upload should be on local disk"
    return path.replace("\\", "/")


async def _run(expression: str, tenant: str = "attacker"):
    from shared.storage import get_storage_backend

    get_storage_backend().write(tenant, "mine.csv", b"id,v\n1,10\n2,20\n")
    pipeline = Pipeline(
        name="probe",
        source=PipelineSource(type=SourceType.FILE, file_name="mine.csv"),
        steps=[ProcessingStep(type=StepType.CUSTOM_SQL, config={"expression": expression})],
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    return await PipelineEngine().execute(pipeline, preview_only=True, tenant=tenant)


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["double-quoted-path", "comma-joined-literal"])
async def test_step_sql_cannot_read_another_tenants_file(tmp_path, monkeypatch, form):
    _isolate_storage(tmp_path, monkeypatch)
    path = _victim_file(tmp_path)
    expression = (
        f'SELECT * FROM "{path}"' if form == "double-quoted-path"
        else f"SELECT b.* FROM {{{{prev}}}} a, '{path}' b"
    )

    run = await _run(expression)

    assert run.status == PipelineStatus.FAILED, run.preview_data
    assert SECRET not in str(run.preview_data)


@pytest.mark.asyncio
async def test_ordinary_step_sql_still_runs(tmp_path, monkeypatch):
    _isolate_storage(tmp_path, monkeypatch)

    run = await _run("SELECT id, v * 2 AS doubled FROM {{prev}} WHERE v > 10")

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.preview_data == [{"id": 2, "doubled": 40}]


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,ext", [("csv", ".csv"), ("parquet", ".parquet"), ("json", ".json")])
async def test_file_sink_still_writes_from_the_locked_down_run(tmp_path, monkeypatch, fmt, ext):
    _isolate_storage(tmp_path, monkeypatch)
    from shared.storage import get_storage_backend

    tenant = f"sink_{fmt}"
    get_storage_backend().write(tenant, "mine.csv", b"id,v\n1,10\n2,20\n")
    name = f"bug276_{fmt}"
    pipeline = Pipeline(
        name="sink",
        source=PipelineSource(type=SourceType.FILE, file_name="mine.csv"),
        steps=[],
        sink=PipelineSink(type=SinkType.FILE, format=fmt, file_name=name),
    )
    out_path = os.path.join(OUTPUT_DIR, tenant_slug(tenant), name + ext)
    try:
        run = await PipelineEngine().execute(pipeline, tenant=tenant)

        assert run.status == PipelineStatus.SUCCESS, run.error
        assert run.rows_written == 2 and os.path.getsize(out_path) > 0
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


# ── the ETL router runs the same guard and had the same hole ───────────────

async def _etl(tmp_path, sql: str, preview_only: bool = True, fmt: str = "csv", name: str | None = None):
    from starlette.requests import Request

    from api_gateway.routers import etl
    from shared.storage import get_storage_backend

    # an unauthenticated request lands in the "default" tenant
    get_storage_backend().write("default", "mine.csv", b"id,v\n1,10\n2,20\n")
    request = Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})
    payload = etl.ETLPipelineRequest(
        source_file="mine.csv", preview_only=preview_only, destination_format=fmt, destination_filename=name,
        transforms=[etl.ETLTransformStep(type="custom_sql", config={"sql": sql})],
    )
    return await etl.etl_execute(payload, request)


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["double-quoted-path", "comma-joined-literal"])
async def test_etl_custom_sql_cannot_read_another_tenants_file(tmp_path, monkeypatch, form):
    _isolate_storage(tmp_path, monkeypatch)
    path = _victim_file(tmp_path)
    sql = (
        f'SELECT * FROM "{path}"' if form == "double-quoted-path"
        else f"SELECT b.* FROM {{{{input}}}} a, '{path}' b"
    )

    out = await _etl(tmp_path, sql)

    assert out["status"] == "error", out
    assert SECRET not in str(out)


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["csv", "parquet", "json"])
async def test_etl_still_transforms_and_writes_its_output(tmp_path, monkeypatch, fmt):
    _isolate_storage(tmp_path, monkeypatch)
    name = f"bug276_etl_{fmt}"

    out = await _etl(tmp_path, "SELECT id, v * 2 AS doubled FROM {{input}}", preview_only=False, fmt=fmt, name=name)

    assert out["status"] == "success", out
    assert out["preview"] == [{"id": 1, "doubled": 20}, {"id": 2, "doubled": 40}]
    out_path = os.path.join(OUTPUT_DIR, tenant_slug("default"), f"{name}.{fmt}")
    try:
        assert out["output"]["file"] == f"{name}.{fmt}"
        assert os.path.getsize(out_path) > 0
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)
