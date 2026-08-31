"""
UASR Service — FastAPI microservice for the self-healing layer
================================================================
Runs on port 8009 and exposes:
  - POST /uasr/ingest       — submit a micro-batch for drift detection & recovery
  - POST /uasr/baseline     — register a reference baseline for a source
  - GET  /uasr/drift/status — list recent drift events (persisted)
  - GET  /uasr/recovery/{id}— details of a recovery attempt (persisted)
  - GET  /uasr/metrics       — Hᵤ & observability dashboard
  - GET  /uasr/metrics/alerts— threshold violation alerts
  - POST /uasr/gate/check    — semantic gate check for a batch
  - POST /uasr/rollback      — rollback a deployed shim
  - GET  /uasr/shims/{source_id} — list deployed shims
  - GET  /uasr/references/{source_id} — reference embedding versions
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.logging_config import get_logger
from shared.service_factory import create_service

from .db import get_session, init_uasr_db
from .drift_detector import DriftDetector
from .mapek_worker import MAPEKConfig, MAPEKWorker
from .metrics import HealingMetricTracker
from .models import (
    BatchPayload,
    DriftEvent,
    HealingMetric,
    RecoveryMode,
    RecoveryRecord,
    RecoveryStatus,
)
from .recovery_loop import RecoveryLoop, RecoveryLoopConfig
from .runtime_config import (
    build_redis_client,
    build_repair_scheduler,
    build_state_store,
    deployment_summary,
    numeric_heal_flags,
    post_heal_validation_batches,
    s18_1_flags,
)
from .semantic_gateway import ReferenceContextMatrix, SemanticGateway

logger = get_logger("uasr.service")


# ────────────────────────────────────────────────────────────────────
# Service-level singletons
# ────────────────────────────────────────────────────────────────────
# Declared above the lifespan so the MAPE-K worker can share the same
# detector / recovery_loop / tracker instances the HTTP endpoints use —
# otherwise drift events seen via Kafka wouldn't show up in /uasr/metrics
# and shims deployed via either path wouldn't apply to the other.

# Deployment mode is env-driven (see runtime_config): the same image runs
# single-node (in-memory state, in-process repair scheduling) or as a fleet
# (Redis-shared state, cross-node repair admission) with no code change.
# A cold replica with UASR_STATE_BACKEND=redis serves any source because the
# baselines live in Redis, written by its peers.
_redis_client = None
if (
    os.getenv("UASR_STATE_BACKEND", "memory").lower() == "redis"
    or os.getenv("UASR_REPAIR_BACKEND", "local").lower() in ("distributed", "redis", "fleet")
):
    _redis_client = build_redis_client()

_detector = DriftDetector(state_store=build_state_store(redis_client=_redis_client))
# Repair-admission backend (local RepairScheduler or DistributedRepairCoordinator);
# started in the lifespan when the MAPE-K worker runs. None => await recoveries
# directly (legacy one-worker-per-process behaviour).
_repair_scheduler = build_repair_scheduler(redis_client=_redis_client)
_matrix = ReferenceContextMatrix()
_gateway = SemanticGateway(matrix=_matrix)
_tracker = HealingMetricTracker()
# S41: supervised self-healing is opt-in via env so existing deployments are
# unchanged. UASR_RISK_TIERED=true holds risky shims for human approval;
# UASR_RECOVERY_MODE=auto|supervised|monitor_only sets how aggressive AUTO is.
_RISK_TIERED = os.getenv("UASR_RISK_TIERED", "false").lower() in ("1", "true", "yes")
try:
    _RECOVERY_MODE = RecoveryMode(os.getenv("UASR_RECOVERY_MODE", "auto").lower())
except ValueError:
    _RECOVERY_MODE = RecoveryMode.AUTO

def _mapek_config() -> MAPEKConfig:
    """Build the worker config from the environment.

    A named seam rather than an inline ``MAPEKConfig()``: the inline version is
    exactly how numeric value-healing stayed unreachable in every deployment --
    the fields defaulted False and no code path outside tests ever set them, so
    nothing failed and nothing healed. This is directly assertable.
    """
    use_numeric_semantics, numeric_auto_heal = numeric_heal_flags()
    use_martingale_detector, use_shim_router, _ = s18_1_flags()
    return MAPEKConfig(
        use_numeric_semantics=use_numeric_semantics,
        numeric_auto_heal=numeric_auto_heal,
        use_martingale_detector=use_martingale_detector,
        use_shim_router=use_shim_router,
    )


_, _, _USE_CAUSAL_RL_EVALUATOR = s18_1_flags()
_POST_HEAL_VALIDATION_BATCHES = post_heal_validation_batches()

_loop = RecoveryLoop(
    detector=_detector,
    config=RecoveryLoopConfig(
        max_iterations=3,
        auto_deploy=True,
        risk_tiered=_RISK_TIERED,
        mode=_RECOVERY_MODE,
        use_causal_rl_evaluator=_USE_CAUSAL_RL_EVALUATOR,
        post_heal_validation_batches=_POST_HEAL_VALIDATION_BATCHES,
    ),
)

# Set on lifespan startup when UASR_MAPEK_ENABLED=true. Held at module
# scope so /uasr/mapek/status and shutdown can reach it.
_mapek_worker: Optional[MAPEKWorker] = None


# ────────────────────────────────────────────────────────────────────
# Lifespan — DB init + MAPE-K worker (opt-in)
# ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(_):
    global _mapek_worker
    await init_uasr_db()
    logger.info("UASR database tables initialised")
    logger.info("UASR deployment mode: %s", deployment_summary())

    # Restore shims that were live before this process started.
    #
    # _deployed_shims is process-local, and apply_shims() consults it on every
    # batch the MAPE-K worker handles. Without this, a restart (or a second
    # replica) came up with an empty registry and silently passed drifted rows
    # through untransformed — the self-healing layer stopped healing and said
    # nothing. Only DEPLOYED records are selected, so a shim a human rolled back
    # via /uasr/rollback (which persists status=ROLLED_BACK) stays rolled back.
    try:
        rows = []
        async for session in get_session():
            rows = (await session.execute(
                select(DriftEvent.source_id, RecoveryRecord.shim_code)
                .join(DriftEvent, RecoveryRecord.drift_event_id == DriftEvent.id)
                .where(
                    RecoveryRecord.status == RecoveryStatus.DEPLOYED.value,
                    RecoveryRecord.shim_code.isnot(None),
                )
                .order_by(RecoveryRecord.created_at.asc())
            )).all()
            break
        by_source: Dict[str, List[str]] = {}
        for source_id, shim_code in rows:
            if source_id and shim_code:
                by_source.setdefault(source_id, []).append(shim_code)
        restored = _loop.hydrate_deployed_shims(by_source)
        # Logged unconditionally, including the zero case: "restored 0 shims" is
        # the line that tells an operator healing is genuinely inactive rather
        # than silently broken.
        logger.info(
            "UASR restored %d deployed shim(s) across %d source(s)",
            restored, len(by_source),
        )
    except Exception as exc:  # noqa: BLE001 — never block startup on this
        logger.error(
            "UASR could not restore deployed shims (%s: %s) — healing is "
            "INACTIVE for previously-healed sources until they are redeployed",
            type(exc).__name__, exc,
        )

    # MAPE-K worker is opt-in because it requires a reachable Kafka
    # cluster — turning it on by default would break every dev box that
    # runs the UASR service for its HTTP API only.
    if os.getenv("UASR_MAPEK_ENABLED", "false").lower() == "true":
        try:
            # Start the repair-admission backend if it needs a background
            # pump (local RepairScheduler). The distributed coordinator polls
            # inside submit() and has no start()/stop().
            if _repair_scheduler is not None and hasattr(_repair_scheduler, "start"):
                await _repair_scheduler.start()
            _mapek_worker = MAPEKWorker(
                _mapek_config(),
                detector=_detector,
                recovery_loop=_loop,
                metrics=_tracker,
                repair_scheduler=_repair_scheduler,
            )
            await _mapek_worker.start()
            logger.info(
                "MAPE-K worker started (topic=%s, group=%s)",
                _mapek_worker._cfg.topic,
                _mapek_worker._cfg.group_id,
            )
        except Exception as exc:
            # A Kafka outage at startup must not crash the rest of the
            # service. Log the failure and leave _mapek_worker=None so
            # /uasr/mapek/status reports the disabled state honestly.
            logger.error("MAPE-K worker failed to start: %s", exc)
            _mapek_worker = None

    try:
        yield
    finally:
        if _mapek_worker is not None:
            try:
                await _mapek_worker.stop()
                logger.info("MAPE-K worker stopped cleanly")
            except Exception as exc:
                logger.warning("MAPE-K worker shutdown raised: %s", exc)
        if _repair_scheduler is not None and hasattr(_repair_scheduler, "stop"):
            try:
                await _repair_scheduler.stop(drain=True)
                logger.info("Repair scheduler stopped cleanly")
            except Exception as exc:
                logger.warning("Repair scheduler shutdown raised: %s", exc)

# ────────────────────────────────────────────────────────────────────
# FastAPI application
# ────────────────────────────────────────────────────────────────────

async def _redis_health_probe() -> None:
    """Health probe: ping Redis when a redis-backed mode is active.

    Registered only when the deployment actually depends on Redis, so a
    single-node (memory/local) deployment keeps a dependency-free /health.
    Raising propagates to /health as a 503 degraded status -- the operator
    signal that shared state / fleet admission has lost its backend.
    """
    if _redis_client is None:
        return
    import asyncio as _asyncio
    await _asyncio.get_event_loop().run_in_executor(None, _redis_client.ping)


async def _db_health_probe() -> str | None:
    """SELECT 1 against UASR's own DB. Returns None on success.

    Unconditional on purpose. The redis probe below is skipped whenever
    UASR_STATE_BACKEND is `memory` — which is exactly what the deployed
    free-tier profile sets — so without this the checks dict was empty, and
    create_service returns a bare {"status": "healthy"} when it has no probes.
    That made /health incapable of failing on the self-healing service itself:
    uasr.db and uasr.duckdb could be corrupt or unwritable and the container
    still reported green to compose, Caddy and any uptime check.
    """
    try:
        from sqlalchemy import text

        async for session in get_session():
            await session.execute(text("SELECT 1"))
            break
        return None
    except Exception as exc:  # noqa: BLE001 — surface the message to the caller
        return f"db unreachable: {type(exc).__name__}: {exc}"


_uasr_health_checks = {"db": _db_health_probe}
if _redis_client is not None:
    _uasr_health_checks["redis"] = _redis_health_probe

app = create_service(
    name="UASR Service",
    service_tag="uasr_service",
    description="Universal Agentic Semantic Recovery — self-healing data pipeline layer",
    lifespan=_lifespan,
    health_checks=_uasr_health_checks or None,
)


# ────────────────────────────────────────────────────────────────────
# DB dependency
# ────────────────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


# ────────────────────────────────────────────────────────────────────
# Request / Response models
# ────────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    source_id: str
    batch_id: str = ""
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    schema_snapshot: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaselineRequest(BaseModel):
    source_id: str
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    schema_snapshot: Optional[Dict[str, Any]] = None


class RollbackRequest(BaseModel):
    source_id: str


class ApprovalRequest(BaseModel):
    """S41: a human approving a held recovery out of PENDING_APPROVAL."""
    approver: str
    note: Optional[str] = None


class RejectionRequest(BaseModel):
    """S41: a human rejecting a held recovery (escalates it)."""
    approver: str
    reason: str


class GateCheckRequest(BaseModel):
    source_id: str
    batch_id: str = ""
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _serialize_drift_event(ev: DriftEvent) -> Dict[str, Any]:
    return {
        "id": ev.id,
        "source_id": ev.source_id,
        "drift_type": ev.drift_type,
        "severity": ev.severity,
        "kl_divergence": ev.kl_divergence,
        "cosine_distance": ev.cosine_distance,
        "details": ev.details,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


def _serialize_recovery(rec: RecoveryRecord) -> Dict[str, Any]:
    return {
        "id": rec.id,
        "drift_event_id": rec.drift_event_id,
        "source_id": rec.source_id,
        "status": rec.status,
        "diagnosis": rec.diagnosis,
        "shim_code": rec.shim_code,
        "generation_method": rec.generation_method,
        "validation_passed": rec.validation_passed,
        "post_kl_divergence": rec.post_kl_divergence,
        "latency_seconds": rec.latency_seconds,
        "error": rec.error,
        "decided_by": rec.decided_by,
        "decision_note": rec.decision_note,
        "decided_at": rec.decided_at.isoformat() if rec.decided_at else None,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "completed_at": rec.completed_at.isoformat() if rec.completed_at else None,
    }


# ────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────

@app.post("/uasr/ingest")
async def ingest_batch(req: IngestRequest, db: AsyncSession = Depends(get_db)):
    """
    Submit a micro-batch for drift detection.
    If drift is detected, the recovery loop runs automatically and
    the event + recovery record are persisted to the database.
    """
    batch = BatchPayload(
        source_id=req.source_id,
        batch_id=req.batch_id or f"batch_{req.source_id}_{uuid.uuid4().hex[:8]}",
        columns=req.columns or (list(req.rows[0].keys()) if req.rows else []),
        rows=req.rows,
        schema_snapshot=req.schema_snapshot,
        metadata=req.metadata,
    )

    gate_decision = _gateway.check(batch)
    drift_result = _detector.detect(batch)

    if not drift_result.drift_detected:
        return {
            "status": "clean",
            "drift_detected": False,
            "gate": gate_decision.to_dict(),
            "batch_id": batch.batch_id,
        }

    # Persist drift event
    event_id = uuid.uuid4().hex[:16]
    drift_event = DriftEvent(
        id=event_id,
        source_id=batch.source_id,
        drift_type=drift_result.drift_type.value if drift_result.drift_type else "unknown",
        severity=drift_result.severity.value if drift_result.severity else "medium",
        kl_divergence=drift_result.kl_divergence,
        cosine_distance=drift_result.cosine_distance,
        drift_vector=drift_result.drift_vector,
        details={"description": drift_result.details, "affected_columns": drift_result.affected_columns},
    )
    db.add(drift_event)
    await db.flush()

    # Run recovery loop
    loop_result = await _loop.run(drift_result, batch)

    # Persist recovery record
    recovery_rec = RecoveryRecord(
        id=loop_result.recovery_id,
        drift_event_id=event_id,
        source_id=batch.source_id,
        status=loop_result.status.value,
        diagnosis=loop_result.diagnosis.hypothesis if loop_result.diagnosis else None,
        shim_code=loop_result.shim.shim_code if loop_result.shim else None,
        generation_method=loop_result.shim.generation_method if loop_result.shim else "template",
        validation_passed=loop_result.shim.validation_passed if loop_result.shim else None,
        post_kl_divergence=loop_result.shim.post_kl_divergence if loop_result.shim else None,
        latency_seconds=loop_result.total_latency_seconds,
        # PENDING_APPROVAL holds stay open (no completed_at) until a human acts.
        completed_at=None if loop_result.status == RecoveryStatus.PENDING_APPROVAL else datetime.now(timezone.utc),
    )
    db.add(recovery_rec)
    await db.commit()

    # Update in-memory metrics tracker
    _tracker.record_from_loop_result(batch.source_id, loop_result)

    return {
        "status": loop_result.status.value,
        "drift_detected": True,
        "drift_type": drift_result.drift_type.value if drift_result.drift_type else None,
        "severity": drift_result.severity.value if drift_result.severity else None,
        "drift_event_id": event_id,
        "recovery_id": loop_result.recovery_id,
        "shim_deployed": loop_result.shim.deployed if loop_result.shim else False,
        "post_kl": loop_result.shim.post_kl_divergence if loop_result.shim else None,
        "latency_seconds": round(loop_result.total_latency_seconds, 3),
        "gate": gate_decision.to_dict(),
    }


@app.post("/uasr/heal")
async def heal_batch(req: IngestRequest, db: AsyncSession = Depends(get_db)):
    """Heal a batch and RETURN THE ROWS. The endpoint any pipeline can attach to.

    This is the difference between a monitor and a self-healing layer.
    ``/uasr/ingest`` detects drift, diagnoses it, generates a shim and files a
    recovery record -- but it hands back a verdict and never touches the
    caller's data, and it does not apply already-deployed shims either.
    Closed-loop healing existed in exactly one place: the Kafka MAPE-K worker.
    Any pipeline that could not publish to Kafka -- Airflow, dbt, Spark, an LLM
    pipeline -- got monitoring and nothing more.

    The sequence below is deliberately the same one mapek_worker.py runs
    (apply known shims -> detect -> recover -> re-apply -> emit healed rows), so
    HTTP and Kafka callers get identical semantics rather than two dialects of
    "healed" that drift apart.

    Why return rows instead of exposing the shim: shims are Python. A dbt model
    or a Spark job cannot execute one, but every pipeline on earth can POST JSON
    and read JSON back. Returning data keeps the integration language-agnostic.

    The response always carries `rows`. On the clean path they are the input
    rows with any standing shims applied; on the drift path they are the rows
    after a freshly deployed shim; and when recovery does NOT deploy -- it
    failed, or a location shift was routed to the human approval queue -- the
    rows come back UNHEALED with ``healed: false`` and a reason. That case is
    the one to read carefully: quietly returning unhealed data as though it were
    fixed is precisely the failure this layer exists to prevent.
    """
    batch = BatchPayload(
        source_id=req.source_id,
        batch_id=req.batch_id or f"heal_{req.source_id}_{uuid.uuid4().hex[:8]}",
        columns=req.columns or (list(req.rows[0].keys()) if req.rows else []),
        rows=req.rows,
        schema_snapshot=req.schema_snapshot,
        metadata=req.metadata,
    )

    # 1. Apply shims already deployed for this source, so drift that was
    #    resolved earlier does not re-fire on every batch.
    standing = len(_loop.get_deployed_shims(batch.source_id))
    if standing:
        batch.rows = _loop.apply_shims(batch.source_id, batch.rows)
        batch.columns = list(batch.rows[0].keys()) if batch.rows else batch.columns

    gate_decision = _gateway.check(batch)
    drift_result = _detector.detect(batch)

    if not drift_result.drift_detected:
        return {
            "status": "clean",
            "healed": bool(standing),
            "shims_applied": standing,
            "drift_detected": False,
            "rows": batch.rows,
            "batch_id": batch.batch_id,
            "gate": gate_decision.to_dict(),
        }

    # 2. New drift: persist the event, then let the recovery loop plan a fix.
    event_id = uuid.uuid4().hex[:16]
    db.add(DriftEvent(
        id=event_id,
        source_id=batch.source_id,
        drift_type=drift_result.drift_type.value if drift_result.drift_type else "unknown",
        severity=drift_result.severity.value if drift_result.severity else "medium",
        kl_divergence=drift_result.kl_divergence,
        cosine_distance=drift_result.cosine_distance,
        drift_vector=drift_result.drift_vector,
        details={
            "description": drift_result.details,
            "affected_columns": drift_result.affected_columns,
        },
    ))
    await db.flush()

    loop_result = await _loop.run(drift_result, batch)

    db.add(RecoveryRecord(
        id=loop_result.recovery_id,
        drift_event_id=event_id,
        source_id=batch.source_id,
        status=loop_result.status.value,
        diagnosis=loop_result.diagnosis.hypothesis if loop_result.diagnosis else None,
        shim_code=loop_result.shim.shim_code if loop_result.shim else None,
        generation_method=loop_result.shim.generation_method if loop_result.shim else "template",
        validation_passed=loop_result.shim.validation_passed if loop_result.shim else None,
        post_kl_divergence=loop_result.shim.post_kl_divergence if loop_result.shim else None,
        latency_seconds=loop_result.total_latency_seconds,
        completed_at=None if loop_result.status == RecoveryStatus.PENDING_APPROVAL
        else datetime.now(timezone.utc),
    ))
    await db.commit()
    _tracker.record_from_loop_result(batch.source_id, loop_result)

    # 3. Only a DEPLOYED shim may touch the caller's data. Anything else
    #    (failed, or held for human approval) returns the rows untransformed
    #    and says so -- see the docstring.
    deployed = loop_result.status == RecoveryStatus.DEPLOYED
    if deployed:
        batch.rows = _loop.apply_shims(batch.source_id, batch.rows)
        batch.columns = list(batch.rows[0].keys()) if batch.rows else batch.columns

    return {
        "status": loop_result.status.value,
        "healed": deployed or bool(standing),
        "shims_applied": len(_loop.get_deployed_shims(batch.source_id)),
        "drift_detected": True,
        "drift_type": drift_result.drift_type.value if drift_result.drift_type else None,
        "severity": drift_result.severity.value if drift_result.severity else None,
        "drift_event_id": event_id,
        "recovery_id": loop_result.recovery_id,
        "shim_deployed": deployed,
        "post_kl": loop_result.shim.post_kl_divergence if loop_result.shim else None,
        "reason": None if deployed else (
            loop_result.diagnosis.hypothesis if loop_result.diagnosis
            else "recovery did not deploy a shim; rows returned unchanged"
        ),
        "rows": batch.rows,
        "latency_seconds": round(loop_result.total_latency_seconds, 3),
        "gate": gate_decision.to_dict(),
    }


@app.post("/uasr/baseline")
async def register_baseline(req: BaselineRequest):
    """Register a reference baseline for a data source."""
    batch = BatchPayload(
        source_id=req.source_id,
        batch_id=f"baseline_{req.source_id}",
        columns=req.columns or (list(req.rows[0].keys()) if req.rows else []),
        rows=req.rows,
        schema_snapshot=req.schema_snapshot,
    )

    _detector.register_baseline(batch.source_id, batch)
    version_id = _gateway.register_baseline(batch, desc="manual-baseline")

    return {
        "status": "registered",
        "source_id": req.source_id,
        "reference_version": version_id,
        "row_count": len(req.rows),
        "columns": batch.columns,
    }


@app.get("/uasr/drift/status")
async def drift_status(
    source_id: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List recent drift events from the persistent database."""
    stmt = select(DriftEvent).order_by(DriftEvent.created_at.desc()).limit(limit)
    if source_id:
        stmt = stmt.where(DriftEvent.source_id == source_id)
    result = await db.execute(stmt)
    events = result.scalars().all()

    # Also include in-memory state for sources without DB events yet
    in_memory = []
    for sid in _detector._baselines.keys():
        if not any(e.source_id == sid for e in events):
            in_memory.append({
                "source_id": sid,
                "has_baseline": True,
                "has_reference_embedding": _gateway.matrix.active_embedding(sid) is not None,
                "deployed_shims": len(_loop.get_deployed_shims(sid)),
                "from_memory": True,
            })

    return {
        "events": [_serialize_drift_event(e) for e in events],
        "in_memory_sources": in_memory,
        "total": len(events),
    }


# ── S41: human-in-the-loop approval queue ────────────────────────────
# Declared BEFORE /uasr/recovery/{recovery_id}: FastAPI matches routes in
# declaration order, so with the parameterised route first this literal path
# never ran — "pending" was captured as a recovery id and the endpoint
# answered 404 "Recovery record 'pending' not found". The whole supervised
# self-healing approval queue was unreachable for that reason alone.
@app.get("/uasr/recovery/pending")
async def pending_approvals(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """List recoveries held in PENDING_APPROVAL, awaiting a human decision."""
    result = await db.execute(
        select(RecoveryRecord)
        .where(RecoveryRecord.status == RecoveryStatus.PENDING_APPROVAL.value)
        .order_by(RecoveryRecord.created_at.desc())
        .limit(limit)
    )
    records = result.scalars().all()
    return {"pending": [_serialize_recovery(r) for r in records], "count": len(records)}


@app.get("/uasr/recovery/{recovery_id}")
async def recovery_detail(recovery_id: str, db: AsyncSession = Depends(get_db)):
    """Get details of a specific recovery attempt from the database."""
    result = await db.execute(
        select(RecoveryRecord).where(RecoveryRecord.id == recovery_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Recovery record '{recovery_id}' not found")
    return {"recovery": _serialize_recovery(rec)}


@app.get("/uasr/drift/{drift_event_id}/recovery")
async def list_recoveries_for_event(drift_event_id: str, db: AsyncSession = Depends(get_db)):
    """List all recovery attempts for a specific drift event."""
    result = await db.execute(
        select(RecoveryRecord)
        .where(RecoveryRecord.drift_event_id == drift_event_id)
        .order_by(RecoveryRecord.created_at.desc())
    )
    records = result.scalars().all()
    return {"recoveries": [_serialize_recovery(r) for r in records], "count": len(records)}


@app.post("/uasr/recovery/{recovery_id}/approve")
async def approve_recovery(
    recovery_id: str, req: ApprovalRequest, db: AsyncSession = Depends(get_db),
):
    """Approve a held recovery: deploy its shim and record the decision."""
    result = await db.execute(
        select(RecoveryRecord).where(RecoveryRecord.id == recovery_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Recovery '{recovery_id}' not found")
    if rec.status != RecoveryStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
            detail=f"Recovery '{recovery_id}' is '{rec.status}', not pending approval",
        )

    # Deploy the human-approved shim back to its source (fail-closed until now).
    if rec.source_id and rec.shim_code:
        _loop.deploy_approved_shim(rec.source_id, rec.shim_code, recovery_id)

    rec.status = RecoveryStatus.DEPLOYED.value
    rec.decided_by = req.approver
    rec.decision_note = req.note
    rec.decided_at = datetime.now(timezone.utc)
    rec.completed_at = rec.decided_at
    await db.commit()
    return {"status": "approved", "recovery": _serialize_recovery(rec)}


@app.post("/uasr/recovery/{recovery_id}/reject")
async def reject_recovery(
    recovery_id: str, req: RejectionRequest, db: AsyncSession = Depends(get_db),
):
    """Reject a held recovery: escalate it for human intervention, no deploy."""
    result = await db.execute(
        select(RecoveryRecord).where(RecoveryRecord.id == recovery_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Recovery '{recovery_id}' not found")
    if rec.status != RecoveryStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
            detail=f"Recovery '{recovery_id}' is '{rec.status}', not pending approval",
        )

    rec.status = RecoveryStatus.ESCALATED.value
    rec.decided_by = req.approver
    rec.decision_note = req.reason
    rec.decided_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "escalated", "recovery": _serialize_recovery(rec)}


@app.get("/uasr/metrics")
async def get_metrics(window_seconds: Optional[float] = None):
    """Compute and return the Hᵤ healing report."""
    report = _tracker.compute(window_seconds)
    return {
        "hu_score": report.hu_score,
        "total_sources": report.total_sources,
        "total_events": report.total_events,
        "resolved_events": report.resolved_events,
        "global_resolution_rate": report.global_resolution_rate,
        "global_avg_latency": report.global_avg_latency,
        "computed_at": report.computed_at,
        "trend": report.trend,
        "per_source": [
            {
                "source_id": s.source_id,
                "total_events": s.total_events,
                "resolved_events": s.resolved_events,
                "failed_events": s.failed_events,
                "avg_latency": s.avg_latency,
                "resolution_rate": s.resolution_rate,
                "healing_contribution": s.healing_contribution,
                "by_drift_type": s.by_drift_type,
            }
            for s in report.per_source
        ],
    }


@app.get("/uasr/metrics/history")
async def get_metrics_history(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Return persisted Hᵤ history for trend analysis."""
    result = await db.execute(
        select(HealingMetric).order_by(HealingMetric.created_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return {
        "history": [
            {
                "id": r.id,
                "domain": r.domain,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "total_drift_events": r.total_drift_events,
                "resolved_anomalies": r.resolved_anomalies,
                "recovery_rate": r.recovery_rate,
                "hu_score": r.hu_score,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "count": len(rows),
    }


@app.get("/uasr/metrics/alerts")
async def get_alerts(hu_floor: float = 0.3, resolution_floor: float = 0.5):
    """Check healing metric alert thresholds."""
    alerts = _tracker.check_alerts(hu_floor, resolution_floor)
    return {"alerts": alerts, "count": len(alerts)}


@app.post("/uasr/gate/check")
async def gate_check(req: GateCheckRequest):
    """Run the semantic gate on a batch without triggering recovery."""
    batch = BatchPayload(
        source_id=req.source_id,
        batch_id=req.batch_id or f"gate_{req.source_id}",
        columns=req.columns or (list(req.rows[0].keys()) if req.rows else []),
        rows=req.rows,
    )
    decision = _gateway.check(batch)
    return decision.to_dict()


@app.post("/uasr/rollback")
async def rollback_shim(req: RollbackRequest, db: AsyncSession = Depends(get_db)):
    """Rollback the most recently deployed shim for a source."""
    success = _loop.rollback_last_shim(req.source_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"No deployed shims found for source '{req.source_id}'",
        )

    # Mark the latest recovery record as rolled back
    result = await db.execute(
        select(RecoveryRecord)
        .where(RecoveryRecord.id.in_(
            select(RecoveryRecord.id)
            .join(DriftEvent, RecoveryRecord.drift_event_id == DriftEvent.id)
            .where(DriftEvent.source_id == req.source_id)
            .order_by(RecoveryRecord.created_at.desc())
            .limit(1)
        ))
    )
    rec = result.scalar_one_or_none()
    if rec:
        rec.status = RecoveryStatus.ROLLED_BACK.value
        await db.commit()

    return {"status": "rolled_back", "source_id": req.source_id}


@app.get("/uasr/shims/{source_id}")
async def list_shims(source_id: str):
    """List all currently deployed shims for a source."""
    shims = _loop.get_deployed_shims(source_id)
    return {
        "source_id": source_id,
        "deployed_shims": len(shims),
        "shims": [{"index": i, "code_preview": s[:200]} for i, s in enumerate(shims)],
    }


@app.get("/uasr/references/{source_id}")
async def list_references(source_id: str):
    """List all reference embedding versions for a source."""
    versions = _gateway.reference_versions(source_id)
    return {"source_id": source_id, "versions": versions}


@app.get("/uasr/sources")
async def list_sources(db: AsyncSession = Depends(get_db)):
    """List all sources that have ever been monitored."""
    result = await db.execute(
        select(DriftEvent.source_id).distinct()
    )
    db_sources = [row[0] for row in result.all()]
    memory_sources = list(_detector._baselines.keys())
    all_sources = list(set(db_sources + memory_sources))
    return {
        "sources": [
            {
                "source_id": sid,
                "has_active_baseline": sid in _detector._baselines,
                "deployed_shims": len(_loop.get_deployed_shims(sid)),
            }
            for sid in all_sources
        ],
        "count": len(all_sources),
    }


@app.get("/uasr/mapek/status")
async def mapek_status() -> Dict[str, Any]:
    """Surface whether the Kafka-fed MAPE-K self-healing loop is running.

    Returns ``running=False`` (with the reason) when ``UASR_MAPEK_ENABLED``
    is unset, when aiokafka isn't installed, or when the broker was
    unreachable at startup. Operators rely on this to confirm the worker
    actually came up after a deploy — service ``/health`` only proves the
    HTTP layer is alive, not that the background consumer is.
    """
    if _mapek_worker is None:
        if os.getenv("UASR_MAPEK_ENABLED", "false").lower() != "true":
            return {"running": False, "reason": "UASR_MAPEK_ENABLED not set"}
        return {"running": False, "reason": "worker failed to start (see service logs)"}
    cfg = _mapek_worker._cfg
    return {
        "running": _mapek_worker._running,
        "paused": _mapek_worker.is_paused,
        "config": {
            "topic": cfg.topic,
            "group_id": cfg.group_id,
            "source_id": cfg.source_id,
            "table": cfg.table_name,
            "duckdb_path": cfg.duckdb_path,
            "batch_size": cfg.batch_size,
            "batch_window_seconds": cfg.batch_window_seconds,
            "pause_on_severity": cfg.pause_on_severity.value,
        },
    }


@app.get("/uasr/deployment")
async def uasr_deployment() -> Dict[str, Any]:
    """Surface the active deployment mode: state backend, repair backend,
    concurrency limits, and this node id. Lets an operator confirm a fleet
    is actually running distributed (Redis state + cross-node admission) vs.
    silently single-node."""
    summary = deployment_summary()
    summary["repair_backend_class"] = (
        type(_repair_scheduler).__name__ if _repair_scheduler is not None else None
    )
    summary["state_store_class"] = (
        type(_detector._store).__name__ if hasattr(_detector, "_store") else None
    )
    return summary


@app.post("/uasr/mapek/resume")
async def mapek_resume() -> Dict[str, Any]:
    """Manual unpause after operator-triaged drift recovery.

    When a recovery loop fails (e.g. shim validation never converges) the
    worker stays paused — offsets preserved — so a human can inspect
    the drift event and explicitly resume.
    """
    if _mapek_worker is None:
        raise HTTPException(status_code=409, detail="MAPE-K worker is not running")
    if not _mapek_worker.is_paused:
        return {"resumed": False, "reason": "worker was not paused"}
    _mapek_worker.resume()
    return {"resumed": True}

# âââ Operator dashboard (static single-page console) âââ
@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def uasr_dashboard() -> Any:
    """Serve the self-contained operator console (polls the REST API)."""
    from pathlib import Path

    from fastapi.responses import HTMLResponse

    html_path = Path(__file__).parent / "static" / "dashboard.html"
    if not html_path.is_file():
        raise HTTPException(status_code=404, detail="dashboard not found")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
