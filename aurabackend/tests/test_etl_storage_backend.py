"""
BUG-035: /etl/preview-source, /etl/execute, and /etl/natural-language used
to read the local tenant upload dir directly (tenant_upload_dir() + a raw
Path), bypassing the S45 StorageBackend abstraction that /api/v1/upload and
every other S45-migrated read path (data_utils.py, files.py) already route
through. Under AURA_STORAGE_BACKEND=s3 a plain upload lands only in S3, so
these three endpoints would 404 on a file they should be able to see.

test_storage_s3_duckdb.py's test_etl_preview_and_execute_read_uploaded_file_from_s3
is the real end-to-end proof (Tier B, needs MinIO). This file is the Tier A
companion: local mode, no external dependency, confirming the same call path
(get_storage_backend().exists()/duckdb_uri(), not a raw filesystem check)
still works byte-for-byte for the everyday local deployment.
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


def test_etl_execute_reads_uploaded_file_via_backend(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/execute", json={
        "source_file": "sales.csv",
        "transforms": [{"type": "aggregate", "config": {
            "group_by": ["region"],
            "aggregations": [{"column": "revenue", "func": "SUM", "alias": "total"}],
        }}],
        "preview_only": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert {row["region"]: row["total"] for row in body["preview"]} == {"N": 150, "S": 200}


def test_etl_execute_unknown_file_404s_not_500(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/execute", json={"source_file": "does_not_exist.csv"})
    assert r.status_code == 404, r.text


def test_etl_natural_language_reads_uploaded_file_via_backend(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/natural-language", json={
        "source_file": "sales.csv",
        "instruction": "sum revenue by region",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Whether an LLM provider is configured varies by environment (CI has
    # none, so this legitimately returns status="error" with an honest "LLM
    # failed" message) -- either way, both the success and error response
    # shapes include "schema", which is what actually proves the source file
    # was read via the backend rather than 404ing before the LLM call.
    assert {c["name"] for c in body["schema"]} == {"region", "revenue"}, body


def test_etl_natural_language_runs_blocking_calls_off_the_event_loop(tmp_path, monkeypatch):
    """Single-uvicorn-worker constraint (see .claude/rules/backend.md "Async
    safety"): smart_load_file/con.execute and llm.generate_json are
    synchronous and must run via asyncio.to_thread so they don't block every
    other tenant's request while a file loads or the LLM round-trips.
    Regression test for the fix -- records which thread each blocking call
    actually executed on and asserts it is NOT the event-loop thread."""
    import threading

    main_thread = threading.current_thread()
    call_threads = {}

    import shared.data_utils as data_utils_module
    import shared.llm_provider as llm_provider_module

    orig_smart_load_file = data_utils_module.smart_load_file

    def tracking_smart_load_file(*args, **kwargs):
        call_threads["smart_load_file"] = threading.current_thread()
        return orig_smart_load_file(*args, **kwargs)

    class FakeLLM:
        def is_available(self):
            return True

        def generate_json(self, prompt):
            call_threads["generate_json"] = threading.current_thread()
            return [{"type": "custom_sql", "description": "x", "config": {"sql": "SELECT * FROM {{input}}"}}]

    monkeypatch.setattr(data_utils_module, "smart_load_file", tracking_smart_load_file)
    monkeypatch.setattr(llm_provider_module, "get_llm", lambda: FakeLLM())

    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/natural-language", json={
        "source_file": "sales.csv",
        "instruction": "sum revenue by region",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body

    assert "smart_load_file" in call_threads, "smart_load_file was never called"
    assert "generate_json" in call_threads, "llm.generate_json was never called"
    assert call_threads["smart_load_file"] is not main_thread, (
        "smart_load_file ran on the event-loop thread -- it must be wrapped in asyncio.to_thread"
    )
    assert call_threads["generate_json"] is not main_thread, (
        "llm.generate_json ran on the event-loop thread -- it must be wrapped in asyncio.to_thread"
    )


def test_etl_execute_runs_blocking_calls_off_the_event_loop(tmp_path, monkeypatch):
    """Single-uvicorn-worker constraint (see .claude/rules/backend.md "Async
    safety"): /etl/execute's smart_load_file, transform materialization
    (CREATE TABLE ... AS), DESCRIBE/preview fetchall, and the COPY ... TO
    destination write are all synchronous DuckDB/file-IO calls and must run
    via asyncio.to_thread so they don't block every other tenant's request
    for the full ETL run. Regression test for the fix -- records which
    thread each blocking call actually executed on and asserts it is NOT
    the event-loop thread."""
    import threading

    main_thread = threading.current_thread()
    call_threads = {}

    import shared.data_utils as data_utils_module

    orig_smart_load_file = data_utils_module.smart_load_file

    def tracking_smart_load_file(*args, **kwargs):
        call_threads["smart_load_file"] = threading.current_thread()
        return orig_smart_load_file(*args, **kwargs)

    monkeypatch.setattr(data_utils_module, "smart_load_file", tracking_smart_load_file)

    import api_gateway.routers.etl as etl_module

    orig_build_transform_sql = etl_module._build_transform_sql

    def tracking_build_transform_sql(*args, **kwargs):
        call_threads["build_transform_sql"] = threading.current_thread()
        return orig_build_transform_sql(*args, **kwargs)

    monkeypatch.setattr(etl_module, "_build_transform_sql", tracking_build_transform_sql)

    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/execute", json={
        "source_file": "sales.csv",
        "transforms": [{"type": "aggregate", "config": {
            "group_by": ["region"],
            "aggregations": [{"column": "revenue", "func": "SUM", "alias": "total"}],
        }}],
        "destination_format": "csv",
        "preview_only": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert body["output"]["file"], "expected a destination file to have been written"

    assert "smart_load_file" in call_threads, "smart_load_file was never called"
    assert "build_transform_sql" in call_threads, "_build_transform_sql was never called"
    assert call_threads["smart_load_file"] is not main_thread, (
        "smart_load_file ran on the event-loop thread -- it must be wrapped in asyncio.to_thread"
    )
    assert call_threads["build_transform_sql"] is not main_thread, (
        "_build_transform_sql (which drives CREATE TABLE/DESCRIBE/COPY) ran on the "
        "event-loop thread -- it must be wrapped in asyncio.to_thread"
    )
