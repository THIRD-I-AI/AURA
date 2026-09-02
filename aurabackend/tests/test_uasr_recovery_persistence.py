"""
Unit tests for ``uasr.recovery_persistence.persist_recovery_row`` --
specifically the ``return_row=True`` option added so the Kafka MAPE-K
worker can thread a live ``(db, recovery_rec)`` into candidate #5's
cross-source auto-heal (``uasr.cross_source_heal.attempt_cross_source_heal``)
without persist_recovery_row closing the session out from under it.

Deliberately imports ONLY ``uasr.recovery_persistence``, ``uasr.db``, and
``uasr.models`` -- never ``uasr.mapek_worker`` -- per
docs/BUG_REGISTRY.md's BUG-008 and recovery_persistence.py's own module
docstring: that module exists specifically so this DB-write logic can be
tested without pulling in RecoveryLoop -> the reflector/actuator agents ->
an LLM-provider client, which is what made a combined DB-engine + import
hang the interpreter at exit in the original investigation.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import delete, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr.db import get_session, init_uasr_db  # noqa: E402
from uasr.models import (  # noqa: E402
    BatchPayload,
    DriftDetectionResult,
    DriftEvent,
    DriftType,
    RecoveryLoopResult,
    RecoveryRecord,
    RecoveryStatus,
    ShimResult,
)
from uasr.recovery_persistence import persist_recovery_row  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original_url = _metadata_db.DATABASE_URL
    original_engine = _metadata_db._engine
    original_factory = _metadata_db._session_factory

    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_persist_test_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None

    yield

    # Dispose the engine THIS fixture created before restoring the original
    # module attributes over it -- otherwise its pooled aiosqlite
    # connections (each a NON-DAEMON worker thread) are orphaned: nothing
    # still references this engine for tests/conftest.py's own
    # pytest_sessionfinish disposal hook to find (it only disposes whatever
    # metadata_store.db._engine points to AT session end, which after the
    # restore below is the pre-test value, not this one), and an orphaned
    # pool blocks interpreter exit at threading._shutdown -- reproduced
    # while writing this test (confirmed hung via `ps`/WMI well after
    # pytest printed its pass/fail summary). dispose() needs a live loop; a
    # throwaway one mirrors the same pattern conftest.py already uses for
    # the identical hazard.
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


@pytest.fixture(autouse=True)
async def _ensure_tables():
    await init_uasr_db()
    yield


def _rec_id() -> str:
    return f"persist_test_{uuid.uuid4().hex[:8]}"


def _batch(source_id: str = "s_kafka") -> BatchPayload:
    return BatchPayload(source_id=source_id, batch_id="batch_kafka_001", rows=[{"a": 1}])


def _drift() -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="s_kafka", batch_id="batch_kafka_001",
        drift_detected=True, drift_type=DriftType.SCHEMA, severity="high",
    )


def _failed_recovery(rec_id: str) -> RecoveryLoopResult:
    return RecoveryLoopResult(
        drift_event_id=rec_id + "_drift",
        recovery_id=rec_id,
        status=RecoveryStatus.FAILED,
        shim=ShimResult(recovery_id=rec_id, shim_code="", generation_method="template", validation_passed=False),
    )


async def _cleanup(rec_id: str) -> None:
    async for session in get_session():
        await session.execute(delete(RecoveryRecord).where(RecoveryRecord.id == rec_id))
        await session.execute(delete(DriftEvent).where(DriftEvent.id == rec_id + "_drift"))
        await session.commit()
        break


class TestPersistRecoveryRow:

    @pytest.mark.asyncio
    async def test_default_call_writes_the_row_and_returns_none(self):
        rec_id = _rec_id()
        try:
            result = await persist_recovery_row(_batch(), _drift(), _failed_recovery(rec_id))
            assert result is None

            async for session in get_session():
                row = (await session.execute(
                    select(RecoveryRecord).where(RecoveryRecord.id == rec_id)
                )).scalar_one()
                assert row.status == RecoveryStatus.FAILED.value
                assert row.source_id == "s_kafka"
                break
        finally:
            await _cleanup(rec_id)

    @pytest.mark.asyncio
    async def test_return_row_hands_back_a_live_session_and_the_committed_row(self):
        rec_id = _rec_id()
        try:
            result = await persist_recovery_row(
                _batch(), _drift(), _failed_recovery(rec_id), return_row=True,
            )
            assert result is not None
            db, recovery_rec = result
            assert recovery_rec.id == rec_id
            assert recovery_rec.status == RecoveryStatus.FAILED.value

            # The row is already committed once (by persist_recovery_row
            # itself) -- prove the returned session is still usable by
            # mutating the SAME row through it and committing again, exactly
            # what attempt_cross_source_heal does with it.
            recovery_rec.status = RecoveryStatus.DEPLOYED.value
            await db.commit()
            # Close before opening a second session against the same SQLite
            # file: GC-driven cleanup of an unclosed async session runs as a
            # separately-scheduled task, not synchronously, and races a
            # following get_session() into "database is locked" -- reproduced
            # while writing this test, hence mapek_worker.py's own
            # _persist_and_maybe_cross_heal explicitly closes too.
            await db.close()

            async for session in get_session():
                row = (await session.execute(
                    select(RecoveryRecord).where(RecoveryRecord.id == rec_id)
                )).scalar_one()
                assert row.status == RecoveryStatus.DEPLOYED.value
                break
        finally:
            await _cleanup(rec_id)

    @pytest.mark.asyncio
    async def test_return_row_false_is_the_default_and_matches_true_apart_from_the_return_value(self):
        """Both modes write an identical row -- return_row only changes
        whether the session/row come back, never what gets persisted."""
        rec_a = _rec_id()
        rec_b = _rec_id()
        try:
            await persist_recovery_row(_batch(), _drift(), _failed_recovery(rec_a))
            result = await persist_recovery_row(
                _batch(), _drift(), _failed_recovery(rec_b), return_row=True,
            )
            assert result is not None
            await result[0].close()

            async for session in get_session():
                row_a = (await session.execute(
                    select(RecoveryRecord).where(RecoveryRecord.id == rec_a)
                )).scalar_one()
                row_b = (await session.execute(
                    select(RecoveryRecord).where(RecoveryRecord.id == rec_b)
                )).scalar_one()
                assert row_a.status == row_b.status == RecoveryStatus.FAILED.value
                assert row_a.source_id == row_b.source_id == "s_kafka"
                break
        finally:
            await _cleanup(rec_a)
            await _cleanup(rec_b)
