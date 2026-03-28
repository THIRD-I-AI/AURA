"""
UASR Service — FastAPI microservice for the self-healing layer
================================================================
Runs on port 8009 and exposes:
  - POST /uasr/ingest       — submit a micro-batch for drift detection & recovery
  - POST /uasr/baseline     — register a reference baseline for a source
  - GET  /uasr/drift/status — list recent drift events
  - GET  /uasr/recovery/{id}— details of a recovery attempt
  - GET  /uasr/metrics       — Hᵤ & observability dashboard
  - GET  /uasr/gate/check    — semantic gate check for a batch
  - POST /uasr/rollback      — rollback a deployed shim
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.service_factory import create_service
from shared.logging_config import get_logger

from .drift_detector import DriftDetector
from .recovery_loop import RecoveryLoop, RecoveryLoopConfig
from .semantic_gateway import SemanticGateway, ReferenceContextMatrix
from .metrics import HealingMetricTracker, RecoveryEvent
from .models import (
    BatchPayload,
    DriftType,
    DriftSeverity,
    RecoveryStatus,
)

logger = get_logger("uasr.service")


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
)


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
# Endpoints
# ────────────────────────────────────────────────────────────────────

@app.post("/uasr/ingest")
async def ingest_batch(req: IngestRequest):
    """
    Submit a micro-batch for drift detection.
    If drift is detected, the recovery loop is triggered automatically.
    """
    batch = BatchPayload(
        source_id=req.source_id,
        batch_id=req.batch_id or f"batch_{req.source_id}",
        columns=req.columns or (list(req.rows[0].keys()) if req.rows else []),
        rows=req.rows,
        schema_snapshot=req.schema_snapshot,
        metadata=req.metadata,
    )

    # ── Semantic gate check ──────────────────────────────────
    gate_decision = _gateway.check(batch)

    # ── Drift detection ──────────────────────────────────────
    drift_result = _detector.detect(batch)

    if not drift_result.drift_detected:
        return {
            "status": "clean",
            "drift_detected": False,
            "gate": gate_decision.to_dict(),
            "batch_id": batch.batch_id,
        }

    # ── Recovery loop ────────────────────────────────────────
    loop_result = await _loop.run(drift_result, batch)

    # ── Record metrics ───────────────────────────────────────
    _tracker.record_from_loop_result(batch.source_id, loop_result)

    return {
        "status": loop_result.status.value,
        "drift_detected": True,
        "drift_type": drift_result.drift_type.value if drift_result.drift_type else None,
        "severity": drift_result.severity.value if drift_result.severity else None,
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

    # Register in drift detector
    _detector.register_baseline(batch.source_id, batch)

    # Register in semantic gateway
    version_id = _gateway.register_baseline(batch, desc="manual-baseline")

    return {
        "status": "registered",
        "source_id": req.source_id,
        "reference_version": version_id,
        "row_count": len(req.rows),
        "columns": batch.columns,
    }


@app.get("/uasr/drift/status")
async def drift_status(source_id: Optional[str] = None, limit: int = 50):
    """
    List recent drift detection results.
    In a full implementation, this would query the DB.
    For now, returns a summary from the detector's state.
    """
    sources = [source_id] if source_id else list(_detector._baselines.keys())

    results = []
    for sid in sources:
        baseline = _detector._baselines.get(sid)
        ref_emb = _gateway.matrix.active_embedding(sid)
        results.append({
            "source_id": sid,
            "has_baseline": baseline is not None,
            "has_reference_embedding": ref_emb is not None,
            "reference_versions": _gateway.reference_versions(sid),
            "deployed_shims": len(_loop.get_deployed_shims(sid)),
        })

    return {"sources": results[:limit], "total": len(results)}


@app.get("/uasr/recovery/{recovery_id}")
async def recovery_detail(recovery_id: str):
    """Get details of a specific recovery attempt."""
    # In a full implementation this queries the DB.
    # For now, return a stub.
    return {
        "recovery_id": recovery_id,
        "note": "Full persistence via metadata store is available when DB is configured.",
    }


@app.get("/uasr/metrics")
async def get_metrics(window_seconds: Optional[float] = None):
    """Compute and return the Hᵤ healing report."""
    report = _tracker.compute(window_seconds)

    # Convert dataclass to dict
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


@app.get("/uasr/metrics/alerts")
async def get_alerts(
    hu_floor: float = 0.3,
    resolution_floor: float = 0.5,
):
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
async def rollback_shim(req: RollbackRequest):
    """Rollback the most recently deployed shim for a source."""
    success = _loop.rollback_last_shim(req.source_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No deployed shims found for source '{req.source_id}'",
        )
    return {"status": "rolled_back", "source_id": req.source_id}


@app.get("/uasr/references/{source_id}")
async def list_references(source_id: str):
    """List all reference embedding versions for a source."""
    versions = _gateway.reference_versions(source_id)
    return {"source_id": source_id, "versions": versions}
