"""Kafka MAPE-K path DB persistence -- a DriftEvent + RecoveryRecord row
per recovery attempt, mirroring exactly what ``POST /uasr/ingest`` does
for the HTTP path (uasr/service.py's ``ingest_batch``).

Before this existed, the Kafka MAPE-K worker fed the in-memory
``HealingMetricTracker`` (for /uasr/metrics dashboards and cross-source
correlation's sibling lookup, which is tracker-based, not DB-based) but
never wrote a row to the DB -- a Kafka-path recovery was invisible to
``GET /uasr/recovery/*``, the Healing Queue UI, and any tooling that
reads recovery history from the DB.

Deliberately its own module, not part of ``uasr/mapek_worker.py``: this
needs only ``uasr.db``/``uasr.models`` (SQLAlchemy models, no side
effects at import time). ``mapek_worker.py`` transitively imports
``RecoveryLoop`` -> the reflector/actuator agents -> an LLM-provider
client, and combining an import of that chain with a test module's own
async DB engine reproducibly hung the interpreter at process exit
(confirmed via isolated repro). Keeping the DB-write logic import-light
means it can be exercised in tests -- or reused anywhere else DB-only
persistence is needed -- without ever pulling in agent/LLM machinery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session, get_session_factory
from .models import (
    BatchPayload,
    DriftDetectionResult,
    DriftEvent,
    RecoveryLoopResult,
    RecoveryRecord,
    RecoveryStatus,
)

logger = logging.getLogger("uasr.recovery_persistence")


async def persist_recovery_row(
    batch: BatchPayload,
    drift: DriftDetectionResult,
    recovery: RecoveryLoopResult,
    *,
    return_row: bool = False,
) -> Optional[Tuple[AsyncSession, RecoveryRecord]]:
    """Persist a DriftEvent + RecoveryRecord for one recovery attempt.

    Called for every recovery attempt (deployed, pending, failed,
    escalated), not just successful ones -- parity with the HTTP path,
    which persists a record for every outcome.

    Best-effort: a DB hiccup here must not crash message processing --
    the in-memory tracker (updated separately) keeps the loop's own
    decision-making unaffected either way.

    ``return_row=True`` returns the just-committed ``(db, recovery_rec)``
    instead of closing out silently -- the caller wanted a live session and
    row it can go on to mutate (candidate #5's cross-source auto-heal, which
    needs to update THIS row in place if a borrowed sibling shim resolves
    the failure, not insert a second one). That path deliberately does NOT
    use ``get_session()`` -- its ``async for db in get_session(): ...;
    break/return`` shape abandons the underlying generator mid-iteration,
    and the generator's own ``finally: await session.close()`` then only
    runs later, whenever something eventually GCs and ``.aclose()``s it.
    That deferred close raced this function's own already-open sessions for
    the same SQLite file and hit "database is locked" (reproduced while
    testing this, see tests/test_uasr_recovery_persistence.py), because the
    resumed generator tries to close a session this function's caller may
    already be using or have closed itself, sometimes from a different
    asyncio loop. Getting a raw session straight from the session factory
    sidesteps that: there is no wrapping generator to abandon, so the
    caller's own ``await db.close()`` (required once done with it) is the
    only close that ever happens -- see
    ``mapek_worker.MAPEKWorker._persist_and_maybe_cross_heal``, which does
    exactly that. Returns None on any exception (still logged, and the
    session is closed before returning) or when ``return_row`` is False.
    """
    if return_row:
        db = get_session_factory()()
        try:
            drift_event, recovery_rec = _build_rows(batch, drift, recovery)
            db.add(drift_event)
            await db.flush()
            db.add(recovery_rec)
            await db.commit()
            return db, recovery_rec
        except Exception as exc:
            logger.warning(
                "Kafka-path RecoveryRecord persistence skipped for recovery_id=%s: %s",
                recovery.recovery_id, exc,
            )
            await db.close()
            return None

    try:
        async for db in get_session():
            drift_event, recovery_rec = _build_rows(batch, drift, recovery)
            db.add(drift_event)
            await db.flush()
            db.add(recovery_rec)
            await db.commit()
            break
    except Exception as exc:
        logger.warning(
            "Kafka-path RecoveryRecord persistence skipped for recovery_id=%s: %s",
            recovery.recovery_id, exc,
        )
    return None


def _build_rows(
    batch: BatchPayload, drift: DriftDetectionResult, recovery: RecoveryLoopResult,
) -> Tuple[DriftEvent, RecoveryRecord]:
    drift_event = DriftEvent(
        id=recovery.drift_event_id,
        source_id=batch.source_id,
        drift_type=drift.drift_type.value if drift.drift_type else "unknown",
        severity=drift.severity.value if drift.severity else "medium",
        kl_divergence=drift.kl_divergence,
        cosine_distance=drift.cosine_distance,
        drift_vector=drift.drift_vector,
        details={"description": drift.details, "affected_columns": drift.affected_columns},
    )
    recovery_rec = RecoveryRecord(
        id=recovery.recovery_id,
        drift_event_id=recovery.drift_event_id,
        source_id=batch.source_id,
        status=recovery.status.value,
        diagnosis=recovery.diagnosis.hypothesis if recovery.diagnosis else None,
        shim_code=recovery.shim.shim_code if recovery.shim else None,
        generation_method=recovery.shim.generation_method if recovery.shim else "template",
        validation_passed=recovery.shim.validation_passed if recovery.shim else None,
        post_kl_divergence=recovery.shim.post_kl_divergence if recovery.shim else None,
        latency_seconds=recovery.total_latency_seconds,
        completed_at=(
            None if recovery.status == RecoveryStatus.PENDING_APPROVAL
            else datetime.now(timezone.utc)
        ),
    )
    return drift_event, recovery_rec
