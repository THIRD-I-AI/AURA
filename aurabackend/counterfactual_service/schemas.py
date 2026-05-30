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

EstimatorMethod = Literal["linear_regression", "ipw", "psm", "double_ml", "forest_dr", "tmle", "iv"]
RefuterName = Literal["random_common_cause", "placebo", "data_subset", "sensitivity"]
Severity = Literal["low", "medium", "high"]


class SensitivityReport(BaseModel):
    """Sprint S23: per-estimate sensitivity analysis.

    Two complementary tools attached to every estimator's effect:

    * **E-value** (VanderWeele & Ding 2017): how strong an unmeasured
      confounder, measured on the risk-ratio scale, would have to be
      to fully explain away the observed effect. Closed-form via
      Chinn 2000 continuous-outcome conversion ``RR ≈ exp(0.91·|d|)``
      followed by ``E = RR + sqrt(RR·(RR-1))``.
    * **Robustness value** (Cinelli & Hazlett 2020): the minimum
      partial-R² strength that an unmeasured confounder would need
      with BOTH treatment and outcome to bring the estimate to zero
      (``RV_1``). Closed-form from ``f = |t|/sqrt(dof)`` via
      ``RV_q = 0.5·(sqrt(f⁴ + 4f²) - f²)``.

    Every field is deterministic given the (point, CI, n_samples,
    n_controls, outcome_sd) tuple — embedding in the artifact hash
    basis preserves Layer 10 byte-identity. ``extreme_scenario_
    adjusted`` is the worst-case adjusted estimate under the
    "1x benchmark" assumption that the unmeasured confounder is as
    strong as the observed partial Y~D relationship.
    """
    e_value_point: float
    e_value_ci: float
    rr_approx: float
    standardised_effect_d: float
    null_crossed: bool
    t_statistic: float
    dof: int
    partial_r2_yd_x: float
    robustness_value: float
    extreme_scenario_adjusted: float


class PropensityDiagnostics(BaseModel):
    """Cross-fitted propensity score distribution for one estimator.

    Sprint 13: surfaces the propensity scores the estimator computed so
    auditors can flag IPW-fragile regions. Propensities near 0 or 1
    blow up the doubly-robust correction term ``(T - e) / [e(1-e)]``
    and silently produce noisy effect estimates. Capturing the
    distribution lets the auditor say "this DR estimate is trustworthy"
    or "this DR estimate had 12% of rows with e>0.95, treat the CI
    width as a floor not a ceiling."

    Quantile keys are deliberately string-typed (``"p05"`` etc.) so
    canonical JSON sort order is stable across sprints — float keys
    would round-trip through Pydantic's dict serialiser in undefined
    order.
    """
    quantiles: Dict[str, float]
    min: float
    max: float
    mean: float
    n_extreme: int          # rows where e < 0.05 or e > 0.95
    n_total: int            # total rows scored


class CounterfactualEstimate(BaseModel):
    method: EstimatorMethod
    point: float
    ci_lower: float
    ci_upper: float
    n_samples: int
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    # Sprint 13: optional for backward compat with persisted artifacts.
    # Only DR-style methods (currently double_ml when econml is
    # available) populate this; DoWhy-routed estimators leave it None.
    # The field IS in the artifact hash basis so propensity drift
    # surfaces as a hash change — that's the contract auditors get.
    propensity_diagnostics: Optional[PropensityDiagnostics] = None
    # Sprint 15: per-row CATE distribution as 10 evenly-spaced quantiles
    # (p05..p95 inclusive at 10 percentile steps). Populated only by
    # ForestDRLearner — the only estimator whose final stage actually
    # gives heterogeneous CATEs across rows. Values are rounded to 6
    # decimals so the canonical-JSON bytes are stable across re-runs
    # and Layer 10 byte-identity holds. The field IS in the hash basis;
    # any drift in the forest's CATE estimates surfaces as a hash
    # change so an auditor can detect non-deterministic model behaviour.
    cate_distribution: Optional[List[float]] = None
    # Sprint 16: which CI contract this estimate's [ci_lower, ci_upper]
    # bracket is in force under. "asymptotic" = the classical
    # statsmodels / bootstrap-of-little-bags interval (S12 LinearDR
    # default, S15 ForestDR default) — coverage holds in large samples
    # under correctly-specified nuisance models. "conformal" = split-
    # conformal on AIPW pseudo-outcomes (Lei & Candès JRSS-B 2021) —
    # coverage holds at the stated level in finite samples regardless
    # of nuisance-model misspecification, at the cost of a slightly
    # wider interval. Auditors should treat "conformal" as the
    # stronger contract; the operator card shows both with a small
    # badge.
    ci_method: Literal["asymptotic", "conformal"] = "asymptotic"
    # Sprint S23: omitted-variable-bias sensitivity attached after the
    # fan-out. None for failed estimates (where point + CI are
    # placeholder zeros and the question doesn't make sense). The
    # field IS in the artifact hash basis — any drift in the sensitivity
    # numbers surfaces as a hash change so an auditor can detect a
    # changed input dataset or DAG.
    sensitivity: Optional[SensitivityReport] = None


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
