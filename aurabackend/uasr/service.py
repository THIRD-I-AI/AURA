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

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.logging_config import get_logger
from shared.service_factory import create_service

from .db import get_session, init_uasr_db
from .drift_detector import DriftDetector
from .metrics import HealingMetricTracker, RecoveryEvent
from .models import (
    BatchPayload,
    DriftEvent,
    DriftSeverity,
    DriftType,
    HealingMetric,
    RecoveryRecord,
    RecoveryStatus,
)
from .recovery_loop import RecoveryLoop, RecoveryLoopConfig
from .semantic_gateway import ReferenceContextMatrix, SemanticGateway

logger = get_logger("uasr.service")


# ────────────────────────────────────────────────────────────────────
# Lifespan — DB init
# ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(_):
    await init_uasr_db()
    logger.info("UASR database tables initialised")
    yield


# ────────────────────────────────────────────────────────────────────
# Service-level singletons
# ────────────────────────────────────────────────────────────────────

_detector = DriftDetector()
_matrix = ReferenceContextMatrix()
_gateway = SemanticGateway(matrix=_matrix)
_tracker = HealingMetricTracker()
_loop = RecoveryLoop(
    detector=_detector,
    config=RecoveryLoopConfig(max_iterations=3, auto_deploy=True),
)

# ────────────────────────────────────────────────────────────────────
# FastAPI application
# ────────────────────────────────────────────────────────────────────

app = create_service(
    name="UASR Service",
    service_tag="uasr_service",
    description="Universal Agentic Semantic Recovery — self-healing data pipeline layer",
    lifespan=_lifespan,
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
        "status": rec.status,
        "diagnosis": rec.diagnosis,
        "shim_code": rec.shim_code,
        "validation_passed": rec.validation_passed,
        "post_kl_divergence": rec.post_kl_divergence,
        "latency_seconds": rec.latency_seconds,
        "error": rec.error,
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
        status=loop_result.status.value,
        diagnosis=loop_result.diagnosis.hypothesis if loop_result.diagnosis else None,
        shim_code=loop_result.shim.shim_code if loop_result.shim else None,
        validation_passed=loop_result.shim.validation_passed if loop_result.shim else None,
        post_kl_divergence=loop_result.shim.post_kl_divergence if loop_result.shim else None,
        latency_seconds=loop_result.total_latency_seconds,
        completed_at=datetime.now(timezone.utc),
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
