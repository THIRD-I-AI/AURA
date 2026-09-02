"""
Integration-shaped test for ``service._attempt_cross_source_heal`` --
the orchestration function ``POST /uasr/ingest`` and ``POST /uasr/heal``
both call when a source's own recovery FAILED and cross-source auto-heal
is on (candidate #5's opt-in extension).

Exercises the real DB write path (mutate + re-commit the already-persisted
RecoveryRecord row) against an isolated temp-file SQLite DB -- same
reasoning as test_uasr_approval_reaper.py: the real dev data/metadata.db
predates later UASR migrations and init_uasr_db()'s create_all never ALTERs
an existing table.

``_loop.run_with_candidate_shim`` and ``_tracker.find_recent_deployed_shim``
are mocked at the service module's singletons -- their own correctness is
covered by test_uasr_cross_source_correlation.py's unit tests; this file
proves the orchestration around them (DB mutation, event recording, when
each path is/isn't taken) end-to-end without driving the ASGI lifespan
(backend.md: TestClient inside a `with` block leaves non-daemon aiosqlite
threads that hang pytest on exit) or requiring a real LLM call.

Each test's insert + heal-attempt call share ONE ``get_session()`` block,
matching how a real request scopes its session -- ``_attempt_cross_source_heal``
commits on the session it's given, so splitting insert and call across two
separately-closed sessions would fail (or rely on generator GC timing, which
this deliberately avoids).
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

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
from uasr.service import _attempt_cross_source_heal  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
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


@pytest.fixture(autouse=True)
async def _ensure_tables():
    await init_uasr_db()
    yield


def _drift() -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="s_failing", batch_id="batch_001",
        drift_detected=True, drift_type=DriftType.SCHEMA, severity="high",
    )


def _batch() -> BatchPayload:
    return BatchPayload(source_id="s_failing", batch_id="batch_001", rows=[{"a": 1}])


async def _fetch(rec_id: str) -> RecoveryRecord:
    async for session in get_session():
        result = await session.execute(select(RecoveryRecord).where(RecoveryRecord.id == rec_id))
        return result.scalar_one()


async def _cleanup(rec_id: str) -> None:
    async for session in get_session():
        await session.execute(delete(RecoveryRecord).where(RecoveryRecord.id == rec_id))
        await session.execute(delete(DriftEvent).where(DriftEvent.id == rec_id + "_drift"))
        await session.commit()
        break


def _rec_id() -> str:
    return f"xheal_test_{uuid.uuid4().hex[:8]}"


class TestAttemptCrossSourceHeal:

    @pytest.mark.asyncio
    async def test_no_sibling_shim_leaves_the_record_unchanged(self):
        rec_id = _rec_id()
        async for session in get_session():
            session.add(DriftEvent(id=rec_id + "_drift", source_id="s_failing", drift_type="schema"))
            rec = RecoveryRecord(
                id=rec_id, drift_event_id=rec_id + "_drift", source_id="s_failing",
                status=RecoveryStatus.FAILED.value, generation_method="template",
                completed_at=datetime.now(timezone.utc),
            )
            session.add(rec)
            await session.commit()

            with patch("uasr.service._tracker") as mock_tracker:
                mock_tracker.find_recent_deployed_shim.return_value = None
                result = await _attempt_cross_source_heal(_drift(), _batch(), rec, session)
            assert result is None
            break

        try:
            row = await _fetch(rec_id)
            assert row.status == RecoveryStatus.FAILED.value
        finally:
            await _cleanup(rec_id)

    @pytest.mark.asyncio
    async def test_sibling_shim_that_fails_validation_leaves_the_record_unchanged(self):
        rec_id = _rec_id()
        async for session in get_session():
            session.add(DriftEvent(id=rec_id + "_drift", source_id="s_failing", drift_type="schema"))
            rec = RecoveryRecord(
                id=rec_id, drift_event_id=rec_id + "_drift", source_id="s_failing",
                status=RecoveryStatus.FAILED.value, generation_method="template",
                completed_at=datetime.now(timezone.utc),
            )
            session.add(rec)
            await session.commit()

            with (
                patch("uasr.service._tracker") as mock_tracker,
                patch("uasr.service._loop") as mock_loop,
            ):
                mock_tracker.find_recent_deployed_shim.return_value = ("s_sibling", "return rows")
                mock_loop.run_with_candidate_shim = AsyncMock(return_value=RecoveryLoopResult(
                    drift_event_id="batch_001",
                    recovery_id="borrowed_rec",
                    status=RecoveryStatus.FAILED,
                    shim=ShimResult(
                        recovery_id="borrowed_rec", shim_code="return rows",
                        generation_method="cross_source_borrowed", validation_passed=False,
                    ),
                ))
                result = await _attempt_cross_source_heal(_drift(), _batch(), rec, session)
            assert result is None
            mock_tracker.record.assert_not_called()
            break

        try:
            row = await _fetch(rec_id)
            assert row.status == RecoveryStatus.FAILED.value
        finally:
            await _cleanup(rec_id)

    @pytest.mark.asyncio
    async def test_successful_cross_heal_updates_the_existing_record(self):
        rec_id = _rec_id()
        recorded_event = None
        async for session in get_session():
            session.add(DriftEvent(id=rec_id + "_drift", source_id="s_failing", drift_type="schema"))
            rec = RecoveryRecord(
                id=rec_id, drift_event_id=rec_id + "_drift", source_id="s_failing",
                status=RecoveryStatus.FAILED.value, generation_method="template",
                completed_at=datetime.now(timezone.utc),
            )
            session.add(rec)
            await session.commit()

            with (
                patch("uasr.service._tracker") as mock_tracker,
                patch("uasr.service._loop") as mock_loop,
            ):
                mock_tracker.find_recent_deployed_shim.return_value = ("s_sibling", "return rows")
                mock_loop.run_with_candidate_shim = AsyncMock(return_value=RecoveryLoopResult(
                    drift_event_id="batch_001",
                    recovery_id="borrowed_rec",
                    status=RecoveryStatus.DEPLOYED,
                    total_latency_seconds=0.05,
                    shim=ShimResult(
                        recovery_id="borrowed_rec", shim_code="return rows",
                        generation_method="cross_source_borrowed",
                        validation_passed=True, post_kl_divergence=0.01, deployed=True,
                    ),
                ))
                result = await _attempt_cross_source_heal(_drift(), _batch(), rec, session)
                mock_tracker.record.assert_called_once()
                recorded_event = mock_tracker.record.call_args[0][0]

            assert result is not None
            assert result.status == RecoveryStatus.DEPLOYED
            break

        try:
            row = await _fetch(rec_id)
            assert row.status == RecoveryStatus.DEPLOYED.value
            assert row.shim_code == "return rows"
            assert row.generation_method == "cross_source_borrowed"
            assert row.validation_passed is True
            assert row.completed_at is not None

            assert recorded_event.source_id == "s_failing"
            assert recorded_event.status == RecoveryStatus.DEPLOYED
            assert recorded_event.shim_code == "return rows"
        finally:
            await _cleanup(rec_id)

    @pytest.mark.asyncio
    async def test_successful_cross_heal_that_holds_for_approval_sets_no_completed_at(self):
        """A borrowed shim held under risk-tiering must not read as 'done'."""
        rec_id = _rec_id()
        async for session in get_session():
            session.add(DriftEvent(id=rec_id + "_drift", source_id="s_failing", drift_type="schema"))
            rec = RecoveryRecord(
                id=rec_id, drift_event_id=rec_id + "_drift", source_id="s_failing",
                status=RecoveryStatus.FAILED.value, generation_method="template",
                completed_at=datetime.now(timezone.utc),
            )
            session.add(rec)
            await session.commit()

            with (
                patch("uasr.service._tracker") as mock_tracker,
                patch("uasr.service._loop") as mock_loop,
            ):
                mock_tracker.find_recent_deployed_shim.return_value = ("s_sibling", "return rows")
                mock_loop.run_with_candidate_shim = AsyncMock(return_value=RecoveryLoopResult(
                    drift_event_id="batch_001",
                    recovery_id="borrowed_rec",
                    status=RecoveryStatus.PENDING_APPROVAL,
                    shim=ShimResult(
                        recovery_id="borrowed_rec", shim_code="return rows",
                        generation_method="cross_source_borrowed", validation_passed=True,
                    ),
                ))
                result = await _attempt_cross_source_heal(_drift(), _batch(), rec, session)

            assert result is not None
            assert result.status == RecoveryStatus.PENDING_APPROVAL
            break

        try:
            row = await _fetch(rec_id)
            assert row.status == RecoveryStatus.PENDING_APPROVAL.value
            assert row.completed_at is None
        finally:
            await _cleanup(rec_id)
