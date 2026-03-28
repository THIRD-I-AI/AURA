"""
UASR Data Models
=================
Pydantic models for API contracts and SQLAlchemy models for persistence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

# Re-use the metadata store's Base so all tables live in the same DB
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from metadata_store.db import Base


# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────

class DriftType(str, Enum):
    SCHEMA = "schema"           # columns added/removed/renamed
    STATISTICAL = "statistical" # distribution shift (KL > ζ)
    SEMANTIC = "semantic"       # embedding distance exceeds threshold
    MISSING = "missing"         # expected source not delivering


class DriftSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecoveryStatus(str, Enum):
    DETECTED = "detected"
    DIAGNOSING = "diagnosing"
    GENERATING_SHIM = "generating_shim"
    VALIDATING = "validating"
    DEPLOYED = "deployed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# ────────────────────────────────────────────────────────────────────
# SQLAlchemy ORM — persisted in metadata DB
# ────────────────────────────────────────────────────────────────────

class DriftEvent(Base):
    """One detected drift event."""
    __tablename__ = "uasr_drift_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    drift_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    kl_divergence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cosine_distance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    drift_vector: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class RecoveryRecord(Base):
    """Tracks a single recovery attempt end-to-end."""
    __tablename__ = "uasr_recovery_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    drift_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="detected")
    diagnosis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    shim_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    shim_language: Mapped[str] = mapped_column(String(16), default="python")
    validation_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    post_kl_divergence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DistributionSnapshot(Base):
    """Column-level distribution baseline for drift comparison."""
    __tablename__ = "uasr_distribution_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    histogram: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)  # {bins, counts}
    mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    null_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    distinct_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sample_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class BatchEmbeddingRecord(Base):
    """Stores per-batch semantic embeddings for the Reference Context Matrix."""
    __tablename__ = "uasr_batch_embeddings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    vector: Mapped[List[float]] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(128), default="hash-projection-256")
    is_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class HealingMetric(Base):
    """Tracks the Universal Healing Coefficient (Hᵤ) over time."""
    __tablename__ = "uasr_healing_metrics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    domain: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total_drift_events: Mapped[int] = mapped_column(Integer, default=0)
    resolved_anomalies: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    recovery_rate: Mapped[float] = mapped_column(Float, default=0.0)
    hu_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ────────────────────────────────────────────────────────────────────
# Pydantic API models
# ────────────────────────────────────────────────────────────────────

class ColumnDistribution(BaseModel):
    column_name: str
    histogram: Dict[str, Any] = Field(default_factory=dict)
    mean: Optional[float] = None
    std: Optional[float] = None
    null_rate: Optional[float] = None
    distinct_count: Optional[int] = None
    sample_size: Optional[int] = None


class BatchPayload(BaseModel):
    """Incoming micro-batch for drift detection."""
    source_id: str
    batch_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    schema_snapshot: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DriftDetectionResult(BaseModel):
    source_id: str
    batch_id: str
    drift_detected: bool = False
    drift_type: Optional[DriftType] = None
    severity: Optional[DriftSeverity] = None
    kl_divergence: Optional[float] = None
    cosine_distance: Optional[float] = None
    affected_columns: List[str] = Field(default_factory=list)
    drift_vector: Dict[str, Any] = Field(default_factory=dict)
    details: str = ""


class DiagnosisResult(BaseModel):
    drift_event_id: str
    root_cause: str = ""
    hypothesis: str = ""
    suggested_action: str = ""
    confidence: float = 0.0


class ShimResult(BaseModel):
    recovery_id: str
    shim_code: str = ""
    language: str = "python"
    validation_passed: bool = False
    post_kl_divergence: Optional[float] = None
    deployed: bool = False
    error: Optional[str] = None


class RecoveryLoopResult(BaseModel):
    drift_event_id: str
    recovery_id: str
    status: RecoveryStatus
    diagnosis: Optional[DiagnosisResult] = None
    shim: Optional[ShimResult] = None
    total_latency_seconds: float = 0.0


class HealingReport(BaseModel):
    domain: str
    period: str
    total_drift_events: int = 0
    resolved_anomalies: int = 0
    recovery_rate: float = 0.0
    avg_latency_seconds: float = 0.0
    hu_score: float = 0.0
    trend: List[Dict[str, Any]] = Field(default_factory=list)
