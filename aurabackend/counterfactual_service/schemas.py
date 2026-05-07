"""
Pydantic types for the Counterfactual Audit Engine.

Every list field uses ``Field(default_factory=list)``. Sort-on-read
happens in the renderer / canonical layer — schemas accept any order so
that engine code can build artifacts incrementally.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Inputs ────────────────────────────────────────────────────────────

class InterventionSpec(BaseModel):
    column: str
    actual: float
    counterfactual: float


class OutcomeSpec(BaseModel):
    column: str
    agg: Literal["sum", "mean", "count"]
    window: Tuple[str, str]   # ISO date strings (inclusive on both ends)


class DAGSpec(BaseModel):
    edges: List[Tuple[str, str]]

    @field_validator("edges")
    @classmethod
    def _no_self_loops(cls, edges: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        for src, dst in edges:
            if src == dst:
                raise ValueError(f"DAG self-loop disallowed: {src!r}")
        return edges


class DatasetRef(BaseModel):
    source_id: str
    where: Optional[str] = None
    limit: Optional[int] = None


Audience = Literal["operator", "auditor", "analyst"]


class CounterfactualQuery(BaseModel):
    question: str
    treatment: InterventionSpec
    outcome: OutcomeSpec
    dag: DAGSpec
    dataset: DatasetRef
    audience: Audience = "operator"


# ── Engine outputs ────────────────────────────────────────────────────

EstimatorMethod = Literal["linear_regression", "ipw", "psm", "double_ml"]
RefuterName = Literal["random_common_cause", "placebo", "data_subset", "sensitivity"]
Severity = Literal["low", "medium", "high"]


class CounterfactualEstimate(BaseModel):
    method: EstimatorMethod
    point: float
    ci_lower: float
    ci_upper: float
    n_samples: int
    elapsed_ms: float = 0.0
    error: Optional[str] = None


class RefutationResult(BaseModel):
    refuter: RefuterName
    estimate_after: Optional[float] = None
    p_value: Optional[float] = None
    passed: bool
    elapsed_ms: float = 0.0
    error: Optional[str] = None


class AdversarialChallenge(BaseModel):
    text: str
    severity: Severity
    suggested_check: Optional[str] = None


class JobStatus(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    job_id: str
    state: Literal["queued", "running", "succeeded", "failed"]
    step: Optional[str] = None
    error: Optional[str] = None


# ── The artifact ──────────────────────────────────────────────────────

class CounterfactualArtifact(BaseModel):
    record_id: str
    query: CounterfactualQuery
    estimates: List[CounterfactualEstimate] = Field(default_factory=list)
    refutations: List[RefutationResult] = Field(default_factory=list)
    challenges: List[AdversarialChallenge] = Field(default_factory=list)
    confidence: Severity
    schema_version: str
    dataset_fingerprint: str
    audit_record_hash: Optional[str] = None
    regenerated_critic: bool = False
    # Sprint 9: ED25519 signature over the canonical-JSON payload
    # (audit_record_hash + signature + rendered are excluded from the
    # signed bytes — they're metadata, not content).
    signature_b64: Optional[str] = None
    signature_status: Literal["signed", "unsigned"] = "unsigned"
    signing_key_source: Optional[str] = None
    rendered: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
