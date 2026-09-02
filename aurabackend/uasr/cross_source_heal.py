"""Cross-source drift correlation auto-heal (candidate #5 of the UASR
gap analysis) -- shared between the HTTP path (uasr/service.py) and the
Kafka MAPE-K worker (uasr/mapek_worker.py).

Extracted from service.py's original ``_attempt_cross_source_heal`` so both
callers run the exact same borrow-and-revalidate logic instead of
maintaining two copies that could silently diverge. ``tracker``/``loop`` are
explicit arguments rather than module-level globals because service.py and
``MAPEKWorker`` each own their own instances (module-scoped singletons vs.
instance attributes) -- this module has no opinion on which; it just uses
whatever it's handed.

Mutates and re-commits the ``recovery_rec``/``db`` the caller already has
open rather than opening its own session: one recovery_id, one row, whose
fields reflect how it was actually resolved, and no new DB engine gets
created inside this module (see docs/BUG_REGISTRY.md BUG-008 -- combining a
fresh async DB engine with an import of uasr.mapek_worker's agent/LLM chain
in the same short-lived process hangs at interpreter exit; this module
sidesteps that by never owning a session).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .metrics import RecoveryEvent
from .models import BatchPayload, DriftSeverity, RecoveryRecord, RecoveryStatus

if TYPE_CHECKING:
    from .metrics import HealingMetricTracker
    from .models import DriftDetectionResult, RecoveryLoopResult
    from .recovery_loop import RecoveryLoop

logger = logging.getLogger("uasr.cross_source_heal")


async def attempt_cross_source_heal(
    drift_result: "DriftDetectionResult",
    batch: BatchPayload,
    recovery_rec: RecoveryRecord,
    db: AsyncSession,
    tracker: "HealingMetricTracker",
    loop: "RecoveryLoop",
    correlation_window_seconds: float,
) -> Optional["RecoveryLoopResult"]:
    """Candidate #5, opt-in: this source's own recovery just FAILED. Before
    giving up, check whether a correlated sibling source already has a
    DEPLOYED shim for the same drift_type, and try it against THIS source's
    batch through the normal sandbox-validation path.

    Returns the new RecoveryLoopResult on success (DEPLOYED or
    PENDING_APPROVAL), or None if no candidate existed or it didn't
    validate -- callers must treat None as "stays FAILED, nothing changed."

    Mutates and re-commits ``recovery_rec`` in place rather than inserting a
    second row: one recovery_id, one row, whose fields reflect how it was
    actually resolved. Never called when auto-heal is off.
    """
    sibling = tracker.find_recent_deployed_shim(
        drift_result.drift_type, batch.source_id, correlation_window_seconds,
    )
    if sibling is None:
        return None
    sibling_source_id, shim_code = sibling

    healed = await loop.run_with_candidate_shim(
        drift_result, batch, shim_code, sibling_source_id,
    )
    if healed.status not in (RecoveryStatus.DEPLOYED, RecoveryStatus.PENDING_APPROVAL):
        logger.info(
            "Cross-source heal did not resolve source=%s (status=%s); leaving original FAILED result",
            batch.source_id, healed.status.value,
        )
        return None

    logger.info(
        "Cross-source heal succeeded: source=%s borrowed from=%s, new_status=%s",
        batch.source_id, sibling_source_id, healed.status.value,
    )
    recovery_rec.status = healed.status.value
    recovery_rec.shim_code = healed.shim.shim_code if healed.shim else None
    recovery_rec.generation_method = healed.shim.generation_method if healed.shim else "cross_source_borrowed"
    recovery_rec.validation_passed = healed.shim.validation_passed if healed.shim else None
    recovery_rec.post_kl_divergence = healed.shim.post_kl_divergence if healed.shim else None
    recovery_rec.latency_seconds = (recovery_rec.latency_seconds or 0.0) + healed.total_latency_seconds
    recovery_rec.completed_at = (
        None if healed.status == RecoveryStatus.PENDING_APPROVAL else datetime.now(timezone.utc)
    )
    await db.commit()

    tracker.record(RecoveryEvent(
        source_id=batch.source_id,
        drift_type=drift_result.drift_type,
        severity=drift_result.severity or DriftSeverity.MEDIUM,
        status=healed.status,
        latency_seconds=healed.total_latency_seconds,
        recovery_id=healed.recovery_id,
        post_kl=healed.shim.post_kl_divergence if healed.shim else 0.0,
        shim_code=healed.shim.shim_code if healed.shim and healed.status == RecoveryStatus.DEPLOYED else None,
    ))
    return healed
