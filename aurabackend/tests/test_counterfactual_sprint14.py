"""
Sprint 14 — Propensity warning + sensitivity surfacing tests.

Covers:

* Pure unit tests on ``_propensity_warning_challenges`` for every
  trigger combination (healthy, extreme-fraction, extreme-boundary,
  estimator without diagnostics, mixed estimators).
* Integration: a synthetic dataset where T is near-perfectly predicted
  by a covariate produces a fragile propensity distribution and the
  engine surfaces the auto-challenge in the artifact.
* Determinism: the auto-challenge text is byte-stable across re-runs
  with identical inputs so Sprint 11's Layer 10 re-execution byte-
  identity still holds.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from counterfactual_service.engine import (
    _propensity_warning_challenges,
    dowhy_available,
    econml_available,
    run_job,
)
from counterfactual_service.schemas import (
    CounterfactualEstimate,
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
    PropensityDiagnostics,
)
from tests._mock_llm import UnifiedMockLLM, install_mock
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset

# ── Helper-level unit tests (no dowhy/econml needed) ─────────────────

def _est(method: str, *, n_extreme: int, n_total: int,
         p05: float, p95: float) -> CounterfactualEstimate:
    return CounterfactualEstimate(
        method=method,  # type: ignore[arg-type]
        point=1.0, ci_lower=0.5, ci_upper=1.5,
        n_samples=n_total,
        propensity_diagnostics=PropensityDiagnostics(
            quantiles={"p05": p05, "p25": 0.4, "p50": 0.5,
                       "p75": 0.6, "p95": p95},
            min=p05, max=p95, mean=(p05 + p95) / 2,
            n_extreme=n_extreme, n_total=n_total,
        ),
    )


def test_healthy_propensity_emits_no_challenges():
    """Centered distribution + no extreme rows → no challenge."""
    est = _est("double_ml", n_extreme=0, n_total=300, p05=0.25, p95=0.78)
    assert _propensity_warning_challenges([est]) == []


def test_extreme_fraction_triggers_challenge():
    """11% of rows in IPW-fragile region → high-severity challenge."""
    est = _est("double_ml", n_extreme=33, n_total=300, p05=0.30, p95=0.70)
    out = _propensity_warning_challenges([est])
    assert len(out) == 1
    assert out[0].severity == "high"
    assert "double_ml" in out[0].text
    assert "11.0%" in out[0].text
    assert out[0].suggested_check is not None


def test_extreme_boundary_distribution_triggers_challenge():
    """p95 too close to 1 → high-severity challenge even with no
    individual rows over the extreme threshold."""
    est = _est("double_ml", n_extreme=0, n_total=300, p05=0.40, p95=0.98)
    out = _propensity_warning_challenges([est])
    assert len(out) == 1
    assert out[0].severity == "high"
    assert "p95=0.980" in out[0].text


def test_estimator_without_diagnostics_skipped():
    """Estimators that didn't compute diagnostics (e.g., DoWhy-routed
    methods) don't get a challenge — there's nothing to flag."""
    est = CounterfactualEstimate(
        method="linear_regression", point=1.0,
        ci_lower=0.5, ci_upper=1.5, n_samples=100,
    )
    assert _propensity_warning_challenges([est]) == []


def test_mixed_estimators_only_extreme_flagged():
    """Only the fragile estimator's challenge appears."""
    healthy = _est("ipw", n_extreme=2, n_total=300, p05=0.20, p95=0.80)
    fragile = _est("double_ml", n_extreme=40, n_total=300, p05=0.15, p95=0.99)
    out = _propensity_warning_challenges([healthy, fragile])
    assert len(out) == 1
    assert "double_ml" in out[0].text


def test_propensity_warning_text_byte_stable_across_calls():
    """Same diagnostics → identical challenge text (Layer 10 contract).

    The hash basis runs canonical_dumps over (severity, text); if the
    text drifted across runs, two engine invocations with identical
    inputs would produce different audit_record_hash values.
    """
    est = _est("double_ml", n_extreme=33, n_total=300, p05=0.12, p95=0.88)
    a = _propensity_warning_challenges([est])
    b = _propensity_warning_challenges([est])
    assert a[0].text == b[0].text
    assert a[0].suggested_check == b[0].suggested_check


def test_zero_total_propensity_skipped():
    """n_total=0 is a defensive case — no division-by-zero, no challenge."""
    est = CounterfactualEstimate(
        method="double_ml", point=1.0, ci_lower=0.5, ci_upper=1.5,
        n_samples=0,
        propensity_diagnostics=PropensityDiagnostics(
            quantiles={"p05": 0.0, "p25": 0.0, "p50": 0.0,
                       "p75": 0.0, "p95": 0.0},
            min=0.0, max=0.0, mean=0.0, n_extreme=0, n_total=0,
        ),
    )
    assert _propensity_warning_challenges([est]) == []


# ── Engine-integration tests (require dowhy + econml) ────────────────

ENGINE_TESTS = pytest.mark.skipif(
    not (dowhy_available() and econml_available()),
    reason="dowhy + econml required for propensity-extreme integration tests",
)


@ENGINE_TESTS
@pytest.mark.asyncio
async def test_healthy_dataset_does_not_emit_propensity_challenge(
    monkeypatch, tmp_path,
):
    """The Sprint 9 synthetic dataset has a well-behaved propensity
    structure (sigmoid of seasonality). No propensity challenge should
    appear in the resulting artifact."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    df = synthetic_dataset(n=500)
    query = CounterfactualQuery(
        question="healthy",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="healthy"),
    )

    artifact = await run_job(query, df=df)
    propensity_challenges = [
        c for c in artifact.challenges
        if "IPW-fragile propensity" in c.text
    ]
    assert propensity_challenges == [], (
        f"healthy data unexpectedly produced propensity warnings: "
        f"{[c.text for c in propensity_challenges]}"
    )


@ENGINE_TESTS
@pytest.mark.asyncio
async def test_degenerate_propensity_dataset_emits_challenge(
    monkeypatch, tmp_path,
):
    """Build a dataset where T is near-perfectly predicted by a
    confounder (covariate × 5 + noise). The cross-fitted propensity
    will concentrate near 0 and 1, n_extreme will exceed the 10%
    threshold, and the auto-challenge must fire with severity=high."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    rng = np.random.default_rng(0xb16b00b5)
    n = 600
    seasonality = rng.standard_normal(n)
    # Treatment is essentially a deterministic function of seasonality:
    # large positive seasonality → T=1, large negative → T=0. Logistic
    # propensity sees this and produces e ~ 0.99 or 0.01.
    logits = 5.0 * seasonality
    treatment = (rng.uniform(size=n) < (1 / (1 + np.exp(-logits)))).astype(int)
    outcome = 1.5 * treatment + 1.0 * seasonality + rng.standard_normal(n)
    df = pd.DataFrame({
        "seasonality": seasonality,
        "treatment": treatment,
        "outcome": outcome,
    })

    query = CounterfactualQuery(
        question="degenerate",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="degenerate"),
    )

    artifact = await run_job(query, df=df)
    propensity_challenges = [
        c for c in artifact.challenges
        if "IPW-fragile propensity" in c.text
    ]
    assert len(propensity_challenges) >= 1, (
        f"degenerate data should have triggered a propensity warning. "
        f"Got challenges: {[c.text for c in artifact.challenges]}. "
        f"Diagnostics: "
        f"{[e.propensity_diagnostics for e in artifact.estimates if e.method == 'double_ml']}"
    )
    assert propensity_challenges[0].severity == "high"
    # The challenge IS in the artifact hash basis, so confidence drops.
    assert artifact.confidence in {"low", "medium"}
