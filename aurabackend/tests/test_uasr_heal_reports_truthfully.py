"""BUG-271: /uasr/heal reported `healed: true` for rows it had not healed.

On the drift path it returned `deployed or bool(standing)`, so a source with ANY earlier
shim reported healed even when the new drift's recovery failed or was held for approval.
And a standing shim that raised was swallowed by apply_shims, so the clean path reported
the batch healed too.

Uses the same isolated metadata DB as tests/test_uasr_approval_reaper.py."""
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
from uasr.models import DriftSeverity, DriftType, RecoveryStatus  # noqa: E402

GOOD_SHIM = "def transform(rows):\n    return [{**r, 'v': r['v'] + 1} for r in rows]\n"
BROKEN_SHIM = "def transform(rows):\n    raise ValueError('shim broke')\n"


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_heal_{uuid.uuid4().hex[:8]}.db")
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


def _stub_detection(monkeypatch, drift_detected: bool) -> None:
    drift = types.SimpleNamespace(
        drift_detected=drift_detected, drift_type=DriftType.SCHEMA, severity=DriftSeverity.HIGH,
        kl_divergence=1.0, cosine_distance=0.0, drift_vector={}, details="d", affected_columns=["v"],
        source_id="x", batch_id="b",
    )
    monkeypatch.setattr(service._detector, "detect", lambda batch, **kw: drift)
    monkeypatch.setattr(service._gateway, "check", lambda batch: types.SimpleNamespace(to_dict=lambda: {}))


async def _heal(source: str) -> dict:
    async with get_session_factory()() as db:
        return await service.heal_batch(
            service.IngestRequest(source_id=source, rows=[{"v": 1}]), _request(), db=db)


def test_apply_shims_counted_says_how_many_actually_applied():
    source = f"src_{uuid.uuid4().hex[:6]}"
    service._loop.hydrate_deployed_shims({source: [GOOD_SHIM, BROKEN_SHIM, GOOD_SHIM]})

    rows, applied, total = service._loop.apply_shims_counted(source, [{"v": 1}])

    assert (applied, total) == (1, 3) and rows == [{"v": 2}]
    assert service._loop.apply_shims(source, [{"v": 1}]) == [{"v": 2}]  # unchanged contract


@pytest.mark.asyncio
async def test_a_standing_shim_that_raises_is_not_reported_as_healed(monkeypatch):
    source = f"src_{uuid.uuid4().hex[:6]}"
    service._loop.hydrate_deployed_shims({source: [BROKEN_SHIM]})
    _stub_detection(monkeypatch, drift_detected=False)

    out = await _heal(source)

    assert out["status"] == "clean"
    assert out["healed"] is False and out["shims_applied"] == 0
    assert "failed to apply" in out["reason"]


@pytest.mark.asyncio
async def test_a_working_standing_shim_is_still_reported_as_healed(monkeypatch):
    source = f"src_{uuid.uuid4().hex[:6]}"
    service._loop.hydrate_deployed_shims({source: [GOOD_SHIM]})
    _stub_detection(monkeypatch, drift_detected=False)

    out = await _heal(source)

    assert out["healed"] is True and out["shims_applied"] == 1 and out["rows"] == [{"v": 2}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RecoveryStatus.FAILED, RecoveryStatus.PENDING_APPROVAL])
async def test_new_drift_that_was_not_deployed_is_not_healed_even_with_a_standing_shim(monkeypatch, status):
    source = f"src_{uuid.uuid4().hex[:6]}"
    service._loop.hydrate_deployed_shims({source: [GOOD_SHIM]})
    _stub_detection(monkeypatch, drift_detected=True)

    async def _recovery_not_deployed(drift, batch):
        return types.SimpleNamespace(
            status=status, recovery_id=f"r_{uuid.uuid4().hex[:8]}", diagnosis=None, shim=None,
            total_latency_seconds=0.01)

    monkeypatch.setattr(service._loop, "run", _recovery_not_deployed)
    monkeypatch.setattr(service._tracker, "record_from_loop_result", lambda *a, **k: None)
    monkeypatch.setattr(service, "_CORRELATION_AUTO_HEAL", False)

    out = await _heal(source)

    assert out["status"] == status.value and out["shim_deployed"] is False
    assert out["healed"] is False, "an earlier shim made an un-recovered batch report healed"
    assert out["standing_shims"] == 1 and out["reason"]


@pytest.mark.asyncio
async def test_a_newly_deployed_shim_does_not_re_run_the_standing_ones(monkeypatch):
    # BUG-344: after a new shim deployed, the whole chain ran again on rows the standing
    # shims had already transformed, so a rescale shim scaled twice (1 -> 10 -> 100 -> 101).
    source = f"src_{uuid.uuid4().hex[:6]}"
    rescale = "def transform(rows):\n    return [{**r, 'v': r['v'] * 10} for r in rows]\n"
    service._loop.hydrate_deployed_shims({source: [rescale]})
    _stub_detection(monkeypatch, drift_detected=True)

    async def _recovery_deployed(drift, batch):
        service._loop._deployed_shims[source].append(GOOD_SHIM)
        return types.SimpleNamespace(
            status=RecoveryStatus.DEPLOYED, recovery_id=f"r_{uuid.uuid4().hex[:8]}", diagnosis=None,
            shim=types.SimpleNamespace(shim_code=GOOD_SHIM, generation_method="template",
                                       validation_passed=True, post_kl_divergence=0.0),
            total_latency_seconds=0.01)

    monkeypatch.setattr(service._loop, "run", _recovery_deployed)
    monkeypatch.setattr(service._tracker, "record_from_loop_result", lambda *a, **k: None)

    out = await _heal(source)

    assert out["rows"] == [{"v": 11}]
    assert out["healed"] is True
