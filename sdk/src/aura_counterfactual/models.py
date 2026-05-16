"""
Pydantic models — vendored from ``aurabackend/counterfactual_service/schemas.py``.

The SDK depends only on the *wire format* of the engine, not on the
backend code. Vendoring keeps installs small (no DoWhy/FastAPI pulled
in) and makes the contract explicit: when the backend ships a new
field, the SDK adds it deliberately rather than picking it up by
import.

Any change here MUST mirror the backend's schema or replay+verify
will fall over. The contract is enforced by ``test_models_match.py``
in the SDK test suite (the test compares the field set against a
fixture pulled from the engine's persisted artifact).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

# ── Inputs ────────────────────────────────────────────────────────────

class InterventionSpec(BaseModel):
    column: str
    actual: float
    counterfactual: float


class OutcomeSpec(BaseModel):
    column: str
    agg: Literal["sum", "mean", "count"]
    window: Tuple[str, str]


class DAGSpec(BaseModel):
    edges: List[Tuple[str, str]]


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
    audience: Audience = "analyst"   # SDK default — analysts want the rich payload


# ── Engine outputs ────────────────────────────────────────────────────

EstimatorMethod = Literal["linear_regression", "ipw", "psm", "double_ml", "forest_dr"]
RefuterName = Literal["random_common_cause", "placebo", "data_subset", "sensitivity"]
Severity = Literal["low", "medium", "high"]


class PropensityDiagnostics(BaseModel):
    """Cross-fitted propensity score distribution for one estimator.

    Vendored from counterfactual_service.schemas.PropensityDiagnostics.
    Populated by DR-style estimators only (currently ``double_ml`` when
    the engine has econml installed); other methods leave it None.
    Auditors look at ``n_extreme / n_total`` and the ``p05`` / ``p95``
    quantiles to judge how IPW-fragile the estimate is — large
    n_extreme means the doubly-robust correction had near-divide-by-
    zero rows and the CI width should be treated as a floor.
    """
    quantiles: Dict[str, float]
    min: float
    max: float
    mean: float
    n_extreme: int
    n_total: int


class CounterfactualEstimate(BaseModel):
    method: EstimatorMethod
    point: float
    ci_lower: float
    ci_upper: float
    n_samples: int
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    # Sprint 13: optional + forward-compat. Older artifacts persisted
    # before S13 don't have this field; ConfigDict(extra="ignore") on
    # the artifact handles any future field additions the same way.
    propensity_diagnostics: Optional[PropensityDiagnostics] = None
    # Sprint 15: per-row CATE distribution as 10 quantiles (p05..p95).
    # Populated only by ForestDRLearner. Values rounded to 6 decimals
    # on the engine side so the SDK's forward-compat round-trip stays
    # byte-stable.
    cate_distribution: Optional[List[float]] = None


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
    artifact: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class VerifyResult(BaseModel):
    record_hash: str
    verified: bool
    signature_status: Literal["signed", "unsigned"]
    signing_key_source: Optional[str] = None
    reason: str = ""


class EngineInfo(BaseModel):
    engine_version: str
    dowhy_available: bool
    signing_available: bool = False
    signing_key_source: Optional[str] = None
    pdf_available: bool = False
    estimators: List[str] = Field(default_factory=list)
    refuters: List[str] = Field(default_factory=list)
    audiences: List[str] = Field(default_factory=list)


# ── The artifact ──────────────────────────────────────────────────────

class CounterfactualArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")    # forward-compatible across S11+ field additions

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
    signature_b64: Optional[str] = None
    signature_status: Literal["signed", "unsigned"] = "unsigned"
    signing_key_source: Optional[str] = None
    rendered: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None

    # ── Notebook-friendly summary properties ──────────────────────────

    @property
    def average_point(self) -> Optional[float]:
        valid = [e.point for e in self.estimates if e.error is None]
        return sum(valid) / len(valid) if valid else None

    @property
    def ci_envelope(self) -> Optional[Tuple[float, float]]:
        valid = [e for e in self.estimates if e.error is None]
        if not valid:
            return None
        return min(e.ci_lower for e in valid), max(e.ci_upper for e in valid)

    @property
    def succeeded_estimators(self) -> List[CounterfactualEstimate]:
        return [e for e in self.estimates if e.error is None]

    @property
    def high_severity_challenges(self) -> List[AdversarialChallenge]:
        return [c for c in self.challenges if c.severity == "high"]

    # ── Jupyter rich-repr — wired in by jupyter.attach_html_repr() ────

    def _repr_html_(self) -> str:
        from .jupyter import artifact_html
        return artifact_html(self)
