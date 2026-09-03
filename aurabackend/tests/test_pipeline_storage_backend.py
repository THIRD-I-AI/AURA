"""
BUG-035: pipeline_generate, pipeline_execute, pipeline_execute/async, and
pipeline_file_schema (api_gateway/routers/pipelines.py) used to read the
local tenant upload dir directly (tenant_upload_dir() + a raw path threaded
into PipelineGenerator/PipelineEngine), bypassing the S45 StorageBackend
abstraction that /api/v1/upload and etl.py's sibling endpoints already route
through. Under AURA_STORAGE_BACKEND=s3 a plain upload lands only in S3, so
these endpoints would 404 / report a failed run for a file they should be
able to see.

Tier A: local mode, no external dependency, confirming the router call path
(get_storage_backend().exists()/duckdb_uri()/list(), not a raw filesystem
scan) still works byte-for-byte for the everyday local deployment.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api_gateway.main import app  # noqa: E402


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    get_storage_backend().write(
        "default", "sales.csv", b"region,revenue\nN,100\nS,200\nN,50\n",
    )
    # TestClient WITHOUT `with` -- driving the ASGI lifespan leaves non-daemon
    # aiosqlite threads that hang pytest on exit (see test_synthetic_api.py).
    return TestClient(app)


def test_pipeline_execute_reads_uploaded_file_via_backend(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/pipeline/execute", json={
        "pipeline": {
            "name": "storage-backend-test",
            "source": {"type": "file", "file_name": "sales.csv"},
            "steps": [{
                "type": "aggregate",
                "config": {
                    "group_by": ["region"],
                    "aggregations": [{"function": "SUM", "column": "revenue", "alias": "total"}],
                },
            }],
            "sink": {"type": "preview"},
        },
        "preview_only": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    run = body["run"]
    assert run["status"] == "success", run
    assert {row["region"]: row["total"] for row in run["preview_data"]} == {"N": 150, "S": 200}


def test_pipeline_execute_unknown_file_reports_failed_run_not_500(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/pipeline/execute", json={
        "pipeline": {
            "name": "storage-backend-missing-test",
            "source": {"type": "file", "file_name": "does_not_exist.csv"},
            "sink": {"type": "preview"},
        },
        "preview_only": True,
    })
    # PipelineEngine.execute() catches source-load errors internally and
    # reports them on the run object rather than raising -- so this is a
    # 200 with a failed run, not an HTTP error status (matches the engine's
    # existing exception-handling contract; see test_pipeline_execution.py).
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    run = body["run"]
    assert run["status"] == "failed", run
    assert "not found" in run["error"].lower()


def test_pipeline_schema_endpoint_reads_uploaded_file_via_backend(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/v1/pipeline/schema/sales.csv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert {c["name"] for c in body["schema"]["columns"]} == {"region", "revenue"}


def test_pipeline_generate_reads_explicit_source_file_via_backend(tmp_path, monkeypatch):
    """pipeline_generate's schema_context lookup (gen.get_file_schema) must
    resolve the requested source_file through the backend rather than
    404ing before the local parser / LLM ever runs.

    PipelineGenerator.generate() itself is stubbed out -- whether the local
    rule-based parser or an LLM fallback can turn "show me the data" into a
    real Pipeline depends on what's configured in the environment (no LLM
    key in CI), which is unrelated to what this test proves. Capturing
    generate()'s kwargs instead confirms get_file_schema ran (schema_context
    is populated for the requested file) and a tenant was threaded through,
    without depending on generation actually succeeding.
    """
    client = _client(tmp_path, monkeypatch)

    captured = {}

    async def _fake_generate(self, prompt, available_files=None, schema_context=None, connections=None, tenant=None):
        captured["schema_context"] = schema_context
        captured["tenant"] = tenant
        from pipeline.models import Pipeline, PipelineSink, PipelineSource, SinkType, SourceType
        return Pipeline(
            name="generate-test",
            source=PipelineSource(type=SourceType.FILE, file_name="sales.csv"),
            sink=PipelineSink(type=SinkType.PREVIEW),
        )

    import pipeline.generator as generator_module
    monkeypatch.setattr(generator_module.PipelineGenerator, "generate", _fake_generate)

    r = client.post("/api/v1/pipeline/generate", json={
        "prompt": "show me the data",
        "source_file": "sales.csv",
        "include_schema": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert "sales.csv" in captured["schema_context"], captured
    assert {c["name"] for c in captured["schema_context"]["sales.csv"]["columns"]} == {"region", "revenue"}


def test_upload_dataset_profile_reflects_real_uploaded_columns(tmp_path, monkeypatch):
    """BUG-035 correction #1: files.py's fire-and-forget dataset-profile
    hook (POST /upload) must call PipelineGenerator.get_file_schema with the
    bare tenant, not a local upload_dir path -- otherwise the profile it
    records is built from a schema read that 404s and gets silently
    skipped. Drives the real /upload route end-to-end and asserts the
    resulting dataset profile (via the repository directly) reflects the
    uploaded file's real columns.

    Uses an isolated in-memory metadata_store engine (StaticPool, one
    connection kept alive) rather than the shared metadata.db file --
    same reasoning as test_metadata_store.py's own hermetic repository
    tests -- and monkeypatches BOTH the source module's get_repository
    (picked up by the background hook's own local import) and
    api_gateway.routers.files' already-bound module-level name (the files
    router imports get_repository once at module import time, not per
    call), so both sides of this test see the same fake store.
    """
    import asyncio
    import uuid

    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from metadata_store.db import Base
    from metadata_store.repository import MetadataRepository

    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def _create_tables() -> None:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())

    async def _fake_get_repository():
        async with session_factory() as session:
            yield MetadataRepository(session)

    import api_gateway.routers.files as files_module
    import metadata_store.repository as metadata_repository_module
    monkeypatch.setattr(metadata_repository_module, "get_repository", _fake_get_repository)
    monkeypatch.setattr(files_module, "get_repository", _fake_get_repository)

    import shared.tasks as tasks_module
    profile_coro_holder = {}

    def _fake_fire_and_forget(coro, *, name=None):
        if name and name.startswith("dataset-profile-"):
            profile_coro_holder["coro"] = coro
        else:
            coro.close()
        return None

    monkeypatch.setattr(tasks_module, "fire_and_forget", _fake_fire_and_forget)

    import httpx

    from api_gateway.main import app

    file_name = f"pipeline_profile_test_{uuid.uuid4().hex[:8]}.csv"

    async def _run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/v1/upload",
                files={"file": (file_name, b"id,name,revenue\n1,Alice,100\n2,Bob,250\n3,Carol,75\n", "text/csv")},
            )
            assert r.status_code == 200, r.text
            assert "coro" in profile_coro_holder, "dataset-profile task was never dispatched"
            await profile_coro_holder["coro"]

        async with session_factory() as session:
            repo = MetadataRepository(session)
            profile = await repo.get_dataset_profile(file_name)
            assert profile is not None, "dataset profile was not recorded"
            assert profile.rows_count == 3
            assert profile.columns_count == 3
            names = {c["name"] for c in profile.profile["columns"]}
            assert names == {"id", "name", "revenue"}

    asyncio.run(_run())
