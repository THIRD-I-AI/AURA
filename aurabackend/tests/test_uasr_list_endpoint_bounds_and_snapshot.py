"""BUG-273 / BUG-274: UASR list endpoints.

274: `limit` went straight into SQL LIMIT with no bounds -- a negative value is "no
limit" on SQLite and a 500 on Postgres -- and one endpoint had no limit at all, while
every row carries full shim code.
273: `_detector._baselines` rebuilds itself from the state store on every access (a
blocking Redis SCAN + GET per source); the handlers read it inline, list_sources once
per listed source.

Uses the same isolated metadata DB as tests/test_uasr_approval_reaper.py."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_lists_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
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
        os.remove(tmp_path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
async def _tables():
    await init_uasr_db()
    yield


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/x"})


# No `with`: the ASGI lifespan must not be driven in tests (backend.md).
@pytest.mark.parametrize("path", [
    "/uasr/drift/status", "/uasr/recovery/pending", "/uasr/metrics/history", "/uasr/drift/evt-1/recovery",
])
@pytest.mark.parametrize("limit", ["-1", "0", "501", "100000"])
def test_out_of_range_limits_are_rejected(path, limit):
    client = TestClient(service.app)
    assert client.get(path, params={"limit": limit}).status_code == 422


class _CountingBaselines:
    """Stands in for the detector: counts how often, and on which thread, the
    baseline map is rebuilt."""

    def __init__(self) -> None:
        self.threads: list = []

    @property
    def _baselines(self) -> dict:
        self.threads.append(threading.current_thread())
        return {"a": object(), "b": object(), "c": object()}


@pytest.mark.asyncio
async def test_list_sources_takes_one_baseline_snapshot_off_the_event_loop(monkeypatch):
    detector = _CountingBaselines()
    monkeypatch.setattr(service, "_detector", detector)

    async with get_session_factory()() as db:
        out = await service.list_sources(_request(), db=db)

    assert {s["source_id"] for s in out["sources"]} >= {"a", "b", "c"}
    assert all(s["has_active_baseline"] for s in out["sources"] if s["source_id"] in {"a", "b", "c"})
    assert len(detector.threads) == 1, "the baseline map was rebuilt once per listed source"
    assert detector.threads[0] is not threading.current_thread()


@pytest.mark.asyncio
async def test_drift_status_takes_its_baseline_snapshot_off_the_event_loop(monkeypatch):
    detector = _CountingBaselines()
    monkeypatch.setattr(service, "_detector", detector)

    async with get_session_factory()() as db:
        await service.drift_status(_request(), source_id=None, limit=50, db=db)

    assert len(detector.threads) == 1 and detector.threads[0] is not threading.current_thread()
