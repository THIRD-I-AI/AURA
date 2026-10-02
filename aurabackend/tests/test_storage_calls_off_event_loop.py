"""BUG-238 / BUG-239 / BUG-241: StorageBackend calls ran inline in async handlers.

On S3 `exists` is a head_object, `list` a paginator and `write` a put_object; locally
they are stat / scan / write_bytes. On the single uvicorn worker any of them stalls
every tenant's requests. Each must run on a worker thread (asyncio.to_thread).
"""
from __future__ import annotations

import os
import sys
import threading
import types

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import chat as chatmod
from api_gateway.routers import etl as etlmod
from tests.test_connections_sync import (  # noqa: F401  (fixtures; _isolated_uploads is autouse)
    V1,
    _isolated_uploads,
    _register_connection,
    client,
    duckdb_source,
)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})


class _RecordingBackend:
    """Stands in for the storage backend and records which thread each call ran on."""

    def __init__(self) -> None:
        self.threads: list = []

    def exists(self, tenant, name) -> bool:
        self.threads.append(threading.current_thread())
        return False

    def list(self, tenant):
        self.threads.append(threading.current_thread())
        raise RuntimeError("stop after the listing")


# ── BUG-241: ETL exists() ──────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda: etlmod.etl_preview_source({"source_file": "a.csv"}, _request()),
    lambda: etlmod.etl_execute(etlmod.ETLPipelineRequest(source_file="a.csv"), _request()),
    lambda: etlmod.etl_from_natural_language(
        etlmod.ETLNaturalLanguageRequest(source_file="a.csv", instruction="drop nulls"), _request()),
], ids=["preview-source", "execute", "natural-language"])
async def test_etl_checks_the_source_file_off_the_event_loop(monkeypatch, call):
    backend = _RecordingBackend()
    monkeypatch.setattr(etlmod, "get_storage_backend", lambda: backend)
    here = threading.current_thread()

    with pytest.raises(HTTPException) as exc:
        await call()

    assert exc.value.status_code == 404
    assert len(backend.threads) == 1 and backend.threads[0] is not here


# ── BUG-238: chat pipeline intent list() ──────────────────────────────────

@pytest.mark.asyncio
async def test_chat_pipeline_intent_lists_uploads_off_the_event_loop(monkeypatch):
    backend = _RecordingBackend()

    async def _schema(con, tenant, use_llm=False):
        return {"tables": {}, "context_text": "", "relationships": []}

    async def _pipeline_intent(self, ctx):
        return types.SimpleNamespace(succeeded=True, output={"intent": "pipeline"})

    monkeypatch.setattr(chatmod, "build_schema_context_cached", _schema)
    monkeypatch.setattr(chatmod.IntentAgent, "execute", _pipeline_intent)
    monkeypatch.setattr(chatmod, "get_storage_backend", lambda: backend)
    here = threading.current_thread()

    resp = await chatmod.chat_endpoint(chatmod.ChatRequest(message="build a pipeline from orders.csv"), _request())

    assert resp.status == "Error"  # the fake listing stops the flow right after it runs
    assert len(backend.threads) == 1 and backend.threads[0] is not here


# ── BUG-239: connection sync write() ──────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_writes_the_parquet_snapshot_off_the_event_loop(client, duckdb_source, monkeypatch):  # noqa: F811
    """Same technique as test_parquet_serialization_runs_off_the_event_loop_thread: the
    TestClient runs the handler on a portal thread, so the loop thread is captured from
    an awaited step of the same handler rather than from the test body."""
    import shared.data_utils as data_utils
    from shared.storage.local import LocalBackend

    loop_threads: list = []
    write_threads: list = []
    real_write = LocalBackend.write
    real_invalidate = data_utils.invalidate_schema_cache

    def _recording_write(self, *args, **kwargs):
        write_threads.append(threading.get_ident())
        return real_write(self, *args, **kwargs)

    async def _recording_invalidate(*args, **kwargs):
        loop_threads.append(threading.get_ident())
        return await real_invalidate(*args, **kwargs)

    monkeypatch.setattr(LocalBackend, "write", _recording_write)
    monkeypatch.setattr(data_utils, "invalidate_schema_cache", _recording_invalidate)
    conn = await _register_connection("default", duckdb_source)

    resp = client.post(f"{V1}/connections/{conn['id']}/sync", json={"table_name": "customers"})

    assert resp.status_code == 200, resp.text
    assert len(write_threads) == 1 and len(loop_threads) == 1
    assert write_threads[0] != loop_threads[0], "the parquet snapshot was written on the event-loop thread"
