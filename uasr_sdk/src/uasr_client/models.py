"""
Typed request/response models for the UASR self-healing API.

These mirror the pydantic request models and JSON responses of the
``aurabackend/uasr/service.py`` FastAPI service. They are intentionally
permissive on responses (``extra="allow"``) so a server that adds fields
does not break older clients.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────
# Requests
# ─────────────────────────────────────────────────────────────────────
class BaselineRequest(BaseModel):
    """Register the reference distribution + schema for a source."""

    source_id: str
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    schema_snapshot: Optional[Dict[str, Any]] = None


class IngestRequest(BaseModel):
    """Push one batch through the detect -> gate -> heal pipeline."""

    source_id: str
    batch_id: str = ""
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    schema_snapshot: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GateCheckRequest(BaseModel):
    """Run the semantic gate on a batch WITHOUT triggering recovery."""

    source_id: str
    batch_id: str = ""
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)


class RollbackRequest(BaseModel):
    source_id: str


class ApprovalRequest(BaseModel):
    """Approve a held recovery out of PENDING_APPROVAL."""

    approver: str
    note: Optional[str] = None


class RejectionRequest(BaseModel):
    """Reject a held recovery (escalates it, no deploy)."""

    approver: str
    reason: str


# ─────────────────────────────────────────────────────────────────────
# Responses (permissive: server may add fields)
# ─────────────────────────────────────────────────────────────────────
class _Resp(BaseModel):
    model_config = ConfigDict(extra="allow")


class GateResult(_Resp):
    allowed: bool
    similarity: Optional[float] = None
    threshold: Optional[float] = None
    source_id: Optional[str] = None
    batch_id: Optional[str] = None
    reason: Optional[str] = None


class IngestResult(_Resp):
    """Response of POST /uasr/ingest.

    ``status`` is ``clean`` for a healthy batch; on drift it carries the
    detection verdict, the recovery/drift IDs, whether a shim was deployed,
    and the post-repair KL.
    """

    status: str
    drift_detected: bool = False
    batch_id: Optional[str] = None
    drift_type: Optional[str] = None
    severity: Optional[str] = None
    drift_event_id: Optional[str] = None
    recovery_id: Optional[str] = None
    shim_deployed: Optional[bool] = None
    post_kl: Optional[float] = None
    latency_seconds: Optional[float] = None
    gate: Optional[GateResult] = None


class BaselineResult(_Resp):
    status: str
    source_id: str
    reference_version: Optional[str] = None
    row_count: Optional[int] = None
    columns: List[str] = Field(default_factory=list)


class DeploymentInfo(_Resp):
    state_backend: str
    repair_backend: str
    mapek_enabled: bool = False
    recovery_mode: Optional[str] = None
    node_id: Optional[str] = None
    state_store_class: Optional[str] = None
    repair_backend_class: Optional[str] = None


class MetricsSnapshot(_Resp):
    """Whatever GET /uasr/metrics returns (Hᵤ, resolution, latency, ...)."""


class SourceInfo(_Resp):
    source_id: str
    has_active_baseline: bool = False
    deployed_shims: int = 0
