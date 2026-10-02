"""BUG-266 / BUG-269: /uasr/rollback marked the newest recovery record of ANY status as
ROLLED_BACK, not the record of the shim it removed. Startup re-deploys every record
still marked DEPLOYED, so the removed shim came back on the next restart -- and the
automatic post-heal rollback persisted nothing at all.

Uses the same isolated metadata DB as tests/test_uasr_approval_reaper.py."""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr.db import get_session, get_session_factory, init_uasr_db  # noqa: E402
from uasr.models import DriftEvent, RecoveryRecord, RecoveryStatus  # noqa: E402
from uasr.recovery_persistence import mark_shim_rolled_back  # noqa: E402

GOOD_SHIM = "def transform(rows):\n    return rows  # the deployed one\n"
OTHER_SHIM = "def transform(rows):\n    return rows  # an older deployed one\n"


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_rollback_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    yield
    # Dispose the engine this fixture created (BUG-008: an orphaned aiosqlite pool
    # blocks interpreter exit), then restore.
    leaked = _metadata_db._engine
    if leaked is not None:
        import asyncio as _asyncio
        loop = _asyncio.new_event_loop()
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


async def _record(source: str, status: RecoveryStatus, shim: str | None, age_minutes: int) -> str:
    rec_id = f"rb_{uuid.uuid4().hex[:8]}"
    async for session in get_session():
        session.add(DriftEvent(id=rec_id + "_drift", source_id=source, drift_type="schema"))
        session.add(RecoveryRecord(
            id=rec_id, drift_event_id=rec_id + "_drift", source_id=source, status=status.value,
            shim_code=shim, created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
        ))
        await session.commit()
        break
    return rec_id


async def _status(rec_id: str) -> str:
    async with get_session_factory()() as session:
        return (await session.execute(
            select(RecoveryRecord.status).where(RecoveryRecord.id == rec_id))).scalar_one()


@pytest.mark.asyncio
async def test_rollback_marks_the_deployed_record_not_a_newer_failed_attempt():
    source = f"src_{uuid.uuid4().hex[:6]}"
    deployed = await _record(source, RecoveryStatus.DEPLOYED, GOOD_SHIM, age_minutes=30)
    later_failed = await _record(source, RecoveryStatus.FAILED, None, age_minutes=5)
    held = await _record(source, RecoveryStatus.PENDING_APPROVAL, "def transform(rows):\n    return []\n", age_minutes=1)

    marked = await mark_shim_rolled_back(source, GOOD_SHIM)

    assert marked == deployed
    assert await _status(deployed) == RecoveryStatus.ROLLED_BACK.value
    # the old code flipped the newest row instead: a held recovery vanished from the
    # approval queue and the removed shim stayed DEPLOYED (re-deployed at startup)
    assert await _status(held) == RecoveryStatus.PENDING_APPROVAL.value
    assert await _status(later_failed) == RecoveryStatus.FAILED.value


@pytest.mark.asyncio
async def test_rollback_picks_the_record_whose_code_was_removed():
    source = f"src_{uuid.uuid4().hex[:6]}"
    older = await _record(source, RecoveryStatus.DEPLOYED, OTHER_SHIM, age_minutes=60)
    newer = await _record(source, RecoveryStatus.DEPLOYED, GOOD_SHIM, age_minutes=10)

    assert await mark_shim_rolled_back(source, OTHER_SHIM) == older
    assert await _status(newer) == RecoveryStatus.DEPLOYED.value


@pytest.mark.asyncio
async def test_other_sources_are_untouched_and_nothing_deployed_is_a_noop():
    source, other = f"src_{uuid.uuid4().hex[:6]}", f"src_{uuid.uuid4().hex[:6]}"
    theirs = await _record(other, RecoveryStatus.DEPLOYED, GOOD_SHIM, age_minutes=1)
    await _record(source, RecoveryStatus.FAILED, None, age_minutes=1)

    assert await mark_shim_rolled_back(source, GOOD_SHIM) is None
    assert await _status(theirs) == RecoveryStatus.DEPLOYED.value


@pytest.mark.asyncio
async def test_the_rollback_endpoint_uses_it(monkeypatch):
    from uasr import service

    source = f"src_{uuid.uuid4().hex[:6]}"
    deployed = await _record(source, RecoveryStatus.DEPLOYED, GOOD_SHIM, age_minutes=30)
    newest_failed = await _record(source, RecoveryStatus.FAILED, None, age_minutes=1)
    service._loop.hydrate_deployed_shims({source: [GOOD_SHIM]})

    async with get_session_factory()() as db:
        await service.rollback_shim(service.RollbackRequest(source_id=source), _anonymous_request(), db=db)

    assert service._loop.get_deployed_shims(source) == []
    assert await _status(deployed) == RecoveryStatus.ROLLED_BACK.value
    assert await _status(newest_failed) == RecoveryStatus.FAILED.value


def _anonymous_request():
    """A request with no authenticated principal: UASR applies no tenant scoping to it."""
    from starlette.requests import Request

    return Request({"type": "http", "headers": []})
