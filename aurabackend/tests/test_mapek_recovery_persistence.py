"""
MAPEKWorker._persist_recovery -- the Kafka MAPE-K path now writes a
DriftEvent + RecoveryRecord row per recovery attempt, matching exactly
what POST /uasr/ingest does for the HTTP path.

Before this, the Kafka path fed the in-memory HealingMetricTracker (for
/uasr/metrics and cross-source correlation's sibling lookup, which is
tracker-based) but never wrote to the DB -- a Kafka-path recovery was
invisible to GET /uasr/recovery/*, the Healing Queue UI, and any tooling
reading recovery history from the DB. Noted as a deferred gap in
docs/superpowers/specs/2026-08-30-uasr-effective-self-healing-gap-analysis.md.

Same isolated-temp-file-SQLite fixture pattern as
test_uasr_service_cross_source_heal.py -- the real dev data/metadata.db
predates later UASR migrations and init_uasr_db()'s create_all never
ALTERs an existing table.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr.db import get_session, init_uasr_db  # noqa: E402
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker  # noqa: E402
from uasr.models import (  # noqa: E402
    BatchPayload,
    DiagnosisResult,
    DriftDetectionResult,
    DriftEvent,
    DriftSeverity,
    DriftType,
    RecoveryLoopResult,
    RecoveryRecord,
    RecoveryStatus,
    ShimResult,
)


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original_url = _metadata_db.DATABASE_URL
    original_engine = _metadata_db._engine
    original_factory = _metadata_db._session_factory

    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_mapek_test_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None

    yield

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


def _worker() -> MAPEKWorker:
    return MAPEKWorker(config=MAPEKConfig())


def _batch(batch_id: str) -> BatchPayload:
    return BatchPayload(source_id="kafka_src", batch_id=batch_id, rows=[{"a": 1}])


def _drift(batch_id: str) -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="kafka_src", batch_id=batch_id,
        drift_detected=True, drift_type=DriftType.SCHEMA, severity=DriftSeverity.HIGH,
        kl_divergence=None, affected_columns=["a"], details="schema changed",
    )


def _recovery(
    batch_id: str, status: RecoveryStatus, generation_method: str = "template",
) -> RecoveryLoopResult:
    recovery_id = f"rec_{uuid.uuid4().hex[:8]}"
    return RecoveryLoopResult(
        drift_event_id=batch_id,
        recovery_id=recovery_id,
        status=status,
        diagnosis=DiagnosisResult(drift_event_id=batch_id, root_cause="test", hypothesis="test-hyp"),
        shim=ShimResult(
            recovery_id=recovery_id, shim_code="def transform(rows): return rows",
            generation_method=generation_method,
            validation_passed=status == RecoveryStatus.DEPLOYED,
            post_kl_divergence=0.0 if status == RecoveryStatus.DEPLOYED else None,
        ),
        total_latency_seconds=0.05,
    )


async def _fetch_recovery(recovery_id: str) -> RecoveryRecord:
    async for session in get_session():
        result = await session.execute(select(RecoveryRecord).where(RecoveryRecord.id == recovery_id))
        return result.scalar_one()


async def _fetch_drift(drift_event_id: str) -> DriftEvent:
    async for session in get_session():
        result = await session.execute(select(DriftEvent).where(DriftEvent.id == drift_event_id))
        return result.scalar_one()


class TestPersistRecovery:

    @pytest.mark.asyncio
    async def test_deployed_recovery_persists_both_rows(self):
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        batch = _batch(batch_id)
        drift = _drift(batch_id)
        recovery = _recovery(batch_id, RecoveryStatus.DEPLOYED)

        await _worker()._persist_recovery(batch, drift, recovery)

        drift_row = await _fetch_drift(batch_id)
        assert drift_row.source_id == "kafka_src"
        assert drift_row.drift_type == "schema"

        rec_row = await _fetch_recovery(recovery.recovery_id)
        assert rec_row.drift_event_id == batch_id
        assert rec_row.status == RecoveryStatus.DEPLOYED.value
        assert rec_row.generation_method == "template"
        assert rec_row.validation_passed is True
        assert rec_row.completed_at is not None

    @pytest.mark.asyncio
    async def test_failed_recovery_also_persists(self):
        """A failed/escalated Kafka-path recovery must be as visible to
        GET /uasr/recovery/* as a deployed one -- parity with the HTTP
        path, which persists a record for every outcome, not just
        successes."""
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        batch = _batch(batch_id)
        drift = _drift(batch_id)
        recovery = _recovery(batch_id, RecoveryStatus.FAILED)

        await _worker()._persist_recovery(batch, drift, recovery)

        rec_row = await _fetch_recovery(recovery.recovery_id)
        assert rec_row.status == RecoveryStatus.FAILED.value
        assert rec_row.completed_at is not None

    @pytest.mark.asyncio
    async def test_pending_approval_leaves_completed_at_null(self):
        """PENDING_APPROVAL holds stay open until a human acts -- same
        completed_at=None convention as the HTTP path."""
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        batch = _batch(batch_id)
        drift = _drift(batch_id)
        recovery = _recovery(batch_id, RecoveryStatus.PENDING_APPROVAL, generation_method="llm")

        await _worker()._persist_recovery(batch, drift, recovery)

        rec_row = await _fetch_recovery(recovery.recovery_id)
        assert rec_row.status == RecoveryStatus.PENDING_APPROVAL.value
        assert rec_row.completed_at is None

    @pytest.mark.asyncio
    async def test_db_failure_is_best_effort_and_does_not_raise(self, monkeypatch):
        """A DB hiccup must not crash message processing -- the in-memory
        tracker (fed separately by _knowledge_update) keeps the loop's own
        decision-making unaffected either way."""
        def _boom():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr("uasr.mapek_worker.get_session_factory", _boom)

        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        recovery = _recovery(batch_id, RecoveryStatus.DEPLOYED)
        # Must not raise.
        await _worker()._persist_recovery(_batch(batch_id), _drift(batch_id), recovery)
