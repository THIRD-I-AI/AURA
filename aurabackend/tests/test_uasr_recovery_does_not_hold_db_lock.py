"""BUG-347: /uasr/ingest and /uasr/heal flushed the DriftEvent INSERT and then awaited the
whole recovery loop (LLM calls, sandbox) before committing. On SQLite -- the deployed
metadata DB -- that held the write lock for minutes, so an approval, the reaper, the
Kafka worker's persistence or another tenant's ingest failed with 'database is locked'."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import uuid

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.models import DriftEvent, DriftSeverity, DriftType, RecoveryStatus  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_lock_{uuid.uuid4().hex[:8]}.db")
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
    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["heal", "ingest"])
async def test_another_writer_is_not_blocked_while_a_recovery_runs(monkeypatch, endpoint):
    drift = types.SimpleNamespace(
        drift_detected=True, drift_type=DriftType.SCHEMA, severity=DriftSeverity.HIGH,
        kl_divergence=1.0, cosine_distance=0.0, drift_vector={}, details="d", affected_columns=["v"],
        source_id="x", batch_id="b",
    )
    monkeypatch.setattr(service._detector, "detect", lambda batch, **kw: drift)
    monkeypatch.setattr(service._gateway, "check", lambda batch: types.SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr(service._tracker, "record_from_loop_result", lambda *a, **k: None)
    monkeypatch.setattr(service, "_CORRELATION_AUTO_HEAL", False)
    other_write: list = []

    async def _slow_recovery(drift_result, batch):
        # Stands in for the minutes of LLM calls: meanwhile, someone else writes.
        async with get_session_factory()() as other:
            other.add(DriftEvent(id=f"other_{uuid.uuid4().hex[:8]}", source_id="other", drift_type="schema"))
            await other.commit()
        other_write.append("ok")
        return types.SimpleNamespace(
            status=RecoveryStatus.FAILED, recovery_id=f"r_{uuid.uuid4().hex[:8]}", diagnosis=None, shim=None,
            total_latency_seconds=0.01)

    monkeypatch.setattr(service._loop, "run", _slow_recovery)
    source = f"src_{uuid.uuid4().hex[:6]}"
    req = service.IngestRequest(source_id=source, rows=[{"v": 1}])

    async with get_session_factory()() as db:
        handler = service.heal_batch if endpoint == "heal" else service.ingest_batch
        await asyncio.wait_for(handler(req, _request(), db=db), timeout=30)

    assert other_write == ["ok"]
