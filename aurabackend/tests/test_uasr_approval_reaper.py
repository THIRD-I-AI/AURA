"""
Approval-queue timeout + escalation (candidate #3 of the UASR self-healing
gap analysis, docs/superpowers/specs/2026-08-30-uasr-effective-self-healing-
gap-analysis.md).

Before this: a PENDING_APPROVAL recovery waited forever if no human acted on
it -- S41's own human-in-the-loop guarantee had no floor. Verifies
``_reap_stale_approvals`` (the single-tick reaper core service._approval_
reaper_loop wraps): a PENDING_APPROVAL older than the timeout escalates
exactly like a human /reject would; one younger than the timeout is left
alone; a non-PENDING_APPROVAL record is never touched.

Points the shared metadata_store engine at a throwaway temp-file SQLite DB
for the duration of this module (same reasoning as conftest.py's
GATEWAY_DATABASE_URL override for the gateway DB): the real dev
data/metadata.db predates the S41 migration that added
RecoveryRecord.source_id and `init_uasr_db()`'s `create_all` never ALTERs an
existing table, so writing real rows against it fails with
"table uasr_recovery_records has no column named source_id". A fresh temp
file always matches the current model definitions.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr.db import get_session, init_uasr_db  # noqa: E402
from uasr.models import DriftEvent, RecoveryRecord, RecoveryStatus  # noqa: E402
from uasr.service import _reap_stale_approvals  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    """Swap the process-wide metadata_store engine to a temp file for this
    module only, and restore the original afterward -- other test files in
    the same process must keep seeing whatever DB they expect."""
    original_url = _metadata_db.DATABASE_URL
    original_engine = _metadata_db._engine
    original_factory = _metadata_db._session_factory

    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_test_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None

    yield

    # Dispose the engine THIS fixture created before restoring the original
    # module attributes over it -- an orphaned aiosqlite connection pool
    # (each connection a NON-DAEMON worker thread) blocks interpreter exit
    # at threading._shutdown once nothing still references it for
    # tests/conftest.py's own pytest_sessionfinish disposal hook to find.
    # See tests/test_uasr_recovery_persistence.py's identical fixture for
    # the full story (reproduced and root-caused there; docs/BUG_REGISTRY.md
    # BUG-008).
    leaked_engine = _metadata_db._engine
    if leaked_engine is not None:
        import asyncio as _asyncio
        _loop = _asyncio.new_event_loop()
        try:
            _loop.run_until_complete(leaked_engine.dispose())
        finally:
            _loop.close()

    _metadata_db.DATABASE_URL = original_url
    _metadata_db._engine = original_engine
    _metadata_db._session_factory = original_factory
    try:
        os.remove(tmp_path)
    except OSError:
        pass


async def _insert_recovery(
    *, status: RecoveryStatus, created_at: datetime,
) -> str:
    rec_id = f"reaper_test_{uuid.uuid4().hex[:8]}"
    async for session in get_session():
        session.add(DriftEvent(
            id=rec_id + "_drift",
            source_id="reaper_test_src",
            drift_type="schema",
        ))
        session.add(RecoveryRecord(
            id=rec_id,
            drift_event_id=rec_id + "_drift",
            source_id="reaper_test_src",
            status=status.value,
            created_at=created_at,
        ))
        await session.commit()
        break
    return rec_id


async def _fetch_status(rec_id: str) -> str:
    async for session in get_session():
        result = await session.execute(
            select(RecoveryRecord.status).where(RecoveryRecord.id == rec_id)
        )
        return result.scalar_one()


async def _cleanup(rec_ids: list[str]) -> None:
    async for session in get_session():
        await session.execute(
            delete(RecoveryRecord).where(RecoveryRecord.id.in_(rec_ids))
        )
        await session.execute(
            delete(DriftEvent).where(DriftEvent.id.in_([r + "_drift" for r in rec_ids]))
        )
        await session.commit()
        break


@pytest.fixture(autouse=True)
async def _ensure_tables():
    await init_uasr_db()
    yield


class TestReapStaleApprovals:

    @pytest.mark.asyncio
    async def test_escalates_only_pending_approval_past_the_timeout(self) -> None:
        now = datetime.now(timezone.utc)
        stale = await _insert_recovery(
            status=RecoveryStatus.PENDING_APPROVAL,
            created_at=now - timedelta(hours=2),
        )
        fresh = await _insert_recovery(
            status=RecoveryStatus.PENDING_APPROVAL,
            created_at=now,
        )
        deployed = await _insert_recovery(
            status=RecoveryStatus.DEPLOYED,
            created_at=now - timedelta(hours=2),
        )
        try:
            escalated_count = await _reap_stale_approvals(timeout_seconds=3600)
            assert escalated_count == 1

            assert await _fetch_status(stale) == RecoveryStatus.ESCALATED.value
            assert await _fetch_status(fresh) == RecoveryStatus.PENDING_APPROVAL.value
            assert await _fetch_status(deployed) == RecoveryStatus.DEPLOYED.value
        finally:
            await _cleanup([stale, fresh, deployed])

    @pytest.mark.asyncio
    async def test_noop_when_nothing_is_stale(self) -> None:
        rec = await _insert_recovery(
            status=RecoveryStatus.PENDING_APPROVAL,
            created_at=datetime.now(timezone.utc),
        )
        try:
            assert await _reap_stale_approvals(timeout_seconds=3600) == 0
            assert await _fetch_status(rec) == RecoveryStatus.PENDING_APPROVAL.value
        finally:
            await _cleanup([rec])
