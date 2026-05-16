"""
Sprint 15 — ForestDRLearner + heterogeneous-effect surfacing tests.

Anchors:
  * Wager, S. & Athey, S. (2018). Estimation and Inference of
    Heterogeneous Treatment Effects using Random Forests. JASA 113(523).
  * Athey, S., Tibshirani, J., & Wager, S. (2019). Generalized Random
    Forests. Annals of Statistics 47(2):1148–1178.

Covers:

* ``forest_dr`` is opt-in: not in the default fan-out so Sprint 9/11/13
  eval-gate assertions counting 4 estimators continue to hold; the
  test invokes it explicitly via ``methods=["forest_dr"]``.
* CATE distribution is a 10-element list of 6-decimal-rounded floats
  in the artifact hash basis. Two engine runs on the same data
  produce identical cate_distribution → identical audit_record_hash.
* Layer 12 (heterogeneous DGP): when the true CATE varies across a
  sub-population indicator, ForestDR's CATE distribution shows
  inter-decile spread larger than LinearDR can express. Both should
  recover the marginal ATE within MAE 0.30; ForestDR additionally
  reveals the heterogeneity.
* Renderer surfaces cate_distribution_summary with heterogeneity flag
  ('low' / 'moderate' / 'high') only when an estimator populated
  cate_distribution.
* Without econml, forest_dr returns a structured error rather than
  silently dropping out.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from counterfactual_service.engine import (
    dowhy_available,
    econml_available,
    run_estimators,
    run_job,
)
from counterfactual_service.renderers import render
from counterfactual_service.schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from tests._mock_llm import UnifiedMockLLM, install_mock
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset

ENGINE_TESTS = pytest.mark.skipif(
    not (dowhy_available() and econml_available()),
    reason="dowhy + econml required for Sprint 15 ForestDR tests",
)


# ── ForestDR is opt-in (not in default fan-out) ──────────────────────

@ENGINE_TESTS
@pytest.mark.asyncio
async def test_forest_dr_is_opt_in_and_returns_estimate():
    """When the caller asks for forest_dr explicitly, the engine
    returns one CounterfactualEstimate with method='forest_dr' and a
    populated cate_distribution. The default fan-out still returns
    exactly the original 4 methods."""
    df = synthetic_dataset(n=400)
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))

    # Default fan-out: unchanged 4 estimators
    default = await run_estimators(df, treatment, outcome, synthetic_dag_full())
    assert {e.method for e in default} == {"linear_regression", "ipw", "psm", "double_ml"}

    # Opt-in: forest_dr alone
    forest_only = await run_estimators(
        df, treatment, outcome, synthetic_dag_full(),
        methods=["forest_dr"],
    )
    assert len(forest_only) == 1
    est = forest_only[0]
    assert est.method == "forest_dr"
    assert est.error is None, f"forest_dr failed: {est.error}"
    assert est.cate_distribution is not None
    assert len(est.cate_distribution) == 10
    # All 6-decimal precision (after the engine's rounding)
    for q in est.cate_distribution:
        assert abs(q - round(q, 6)) < 1e-12
    # Quantiles are monotonically non-decreasing
    assert est.cate_distribution == sorted(est.cate_distribution)


# ── Determinism: byte-stable CATE distribution + hash basis ──────────

@ENGINE_TESTS
@pytest.mark.asyncio
async def test_forest_dr_cate_distribution_is_byte_stable(monkeypatch, tmp_path):
    """Same inputs → same forest fit → same CATE quantiles → same
    audit_record_hash. The cate_distribution field is in the hash
    basis (not in _HASH_EXCLUDE_FIELDS), so any non-determinism in
    the forest would surface as a Layer-10 break.

    Note: this test only runs ForestDR (no other estimators) to
    keep it focused; full Layer-10 byte-identity for the canonical
    4-estimator fan-out is covered by Layer 10 itself in S11.
    """
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setenv("AURA_CRITIC_CACHE_DIR", str(tmp_path / "cc"))

    df = synthetic_dataset(n=300, seed=0xfeed_dead)
    query = CounterfactualQuery(
        question="forest_byte_stable",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="forest_byte_stable"),
    )

    # Manually pass methods=["forest_dr"] by hitting the engine. run_job
    # uses the default fan-out, so for this test we exercise
    # run_estimators directly under run_job semantics with a single
    # method by patching the default through the env path:
    #   simplest: just use the artifact's forest_dr estimate via direct
    #   call and assert identical quantiles. The hash-basis assertion
    #   is independently covered by Sprint 13's hash-basis test.
    treatment = query.treatment
    outcome = query.outcome
    dag = query.dag.model_dump()

    a = await run_estimators(df, treatment, outcome, dag, methods=["forest_dr"])
    b = await run_estimators(df, treatment, outcome, dag, methods=["forest_dr"])
    est_a = a[0]
    est_b = b[0]
    assert est_a.error is None and est_b.error is None
    assert est_a.cate_distribution == est_b.cate_distribution, (
        f"CATE distribution drifted across runs:\n"
        f"  a={est_a.cate_distribution}\n"
        f"  b={est_b.cate_distribution}"
    )
    # Point estimate should match to 6 decimals too — forest fit is
    # seed-pinned. Use 6dp not full bit equality because numpy mean
    # over a list of 6dp-rounded values can drift in the 7th decimal.
    assert round(est_a.point, 6) == round(est_b.point, 6)


# ── Layer 12: heterogeneous DGP, ForestDR vs LinearDR ────────────────

@ENGINE_TESTS
@pytest.mark.asyncio
async def test_layer12_forest_dr_recovers_heterogeneity(monkeypatch, tmp_path):
    """Heterogeneous-effect DGP. Two latent sub-populations with
    treatment effects 0.5 and 2.5; the marginal ATE is 1.5 (matching
    the homogeneous Sprint 9 fixture so the existing eval-gate point
    bounds still apply).

    LinearDR's linear-in-X final stage collapses both sub-populations
    onto a single point; ForestDR's non-parametric stage exposes the
    bimodal CATE distribution as a wide inter-decile spread.

    Layer 12 contract: ForestDR's inter-decile spread (p95 - p05) is
    at least 1.0 on this DGP, more than 2× LinearDR's CI half-width.
    Both estimators' point estimates should land within MAE 0.30 of
    the true marginal ATE of 1.5.
    """
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    rng = np.random.default_rng(0xCAFE_F00D)
    n = 800
    # Heterogeneity covariate — a binary group flag the forest can
    # split on. Linear-in-X CANNOT express the difference because the
    # group flag enters the outcome linearly the same way for both
    # arms — only the CATE varies.
    group = (rng.uniform(size=n) > 0.5).astype(int)
    seasonality = rng.standard_normal(n)
    propensity = 1.0 / (1.0 + np.exp(-0.6 * seasonality))
    treatment = (rng.uniform(size=n) < propensity).astype(int)
    # Effect = 0.5 in group 0, 2.5 in group 1 → mean 1.5 ATE
    effect = 0.5 + 2.0 * group
    outcome = effect * treatment + 1.0 * seasonality + 0.5 * group + rng.standard_normal(n)
    df = pd.DataFrame({
        "seasonality": seasonality,
        "group": group,
        "treatment": treatment,
        "outcome": outcome,
    })

    treatment_spec = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome_spec = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))
    # DAG includes both seasonality and group as confounders
    dag = {"edges": [
        ["seasonality", "outcome"],
        ["seasonality", "treatment"],
        ["group", "outcome"],
        ["group", "treatment"],
        ["treatment", "outcome"],
    ]}

    # Run both estimators side by side
    linear_results = await run_estimators(
        df, treatment_spec, outcome_spec, dag, methods=["double_ml"],
    )
    forest_results = await run_estimators(
        df, treatment_spec, outcome_spec, dag, methods=["forest_dr"],
    )
    linear = linear_results[0]
    forest = forest_results[0]
    assert linear.error is None, linear.error
    assert forest.error is None, forest.error

    # Both recover the marginal ATE within MAE 0.30 (true=1.5)
    assert abs(linear.point - 1.5) < 0.30, (
        f"LinearDR off by {abs(linear.point - 1.5):.3f}, point={linear.point:.3f}"
    )
    assert abs(forest.point - 1.5) < 0.30, (
        f"ForestDR off by {abs(forest.point - 1.5):.3f}, point={forest.point:.3f}"
    )

    # ForestDR exposes heterogeneity that LinearDR can't.
    # Inter-decile spread on the forest's per-row CATE vector should
    # be at least 1.0 (≈ half the true range 0.5 to 2.5).
    assert forest.cate_distribution is not None
    forest_spread = forest.cate_distribution[-1] - forest.cate_distribution[0]
    assert forest_spread > 1.0, (
        f"ForestDR failed to surface heterogeneity: spread={forest_spread:.3f}, "
        f"quantiles={forest.cate_distribution}"
    )

    # LinearDR's CI is the analyst's only window into uncertainty.
    # ForestDR's inter-decile spread should comfortably exceed that
    # CI width — that's the visualisation advantage.
    linear_ci_half = (linear.ci_upper - linear.ci_lower) / 2
    assert forest_spread > 2 * linear_ci_half, (
        f"ForestDR spread {forest_spread:.3f} should be >2x linear CI "
        f"half-width {linear_ci_half:.3f}"
    )


# ── Renderer surfaces cate_distribution_summary ──────────────────────

@ENGINE_TESTS
@pytest.mark.asyncio
async def test_operator_view_carries_cate_distribution_summary(
    monkeypatch, tmp_path,
):
    """When forest_dr ran, render(art, 'operator') includes a
    cate_distribution_summary block with quantiles + heterogeneity
    flag. When only double_ml ran, the key is absent."""
    install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))

    df = synthetic_dataset(n=300)
    treatment_spec = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome_spec = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))
    dag = synthetic_dag_full()

    # Run both individually and assemble a fake artifact for the
    # renderer test (avoids spinning up full run_job).
    from counterfactual_service.engine import score_confidence
    from counterfactual_service.schemas import CounterfactualArtifact, CounterfactualQuery

    forest = await run_estimators(df, treatment_spec, outcome_spec, dag, methods=["forest_dr"])
    linear = await run_estimators(df, treatment_spec, outcome_spec, dag, methods=["double_ml"])

    q = CounterfactualQuery(
        question="render_test",
        treatment=treatment_spec,
        outcome=outcome_spec,
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="render_test"),
    )
    art_with_forest = CounterfactualArtifact(
        record_id="ca_test",
        query=q,
        estimates=forest + linear,
        refutations=[],
        challenges=[],
        confidence=score_confidence(forest + linear, [], []),
        schema_version="v1",
        dataset_fingerprint="a" * 64,
        audit_record_hash="b" * 64,
    )
    art_linear_only = CounterfactualArtifact(
        record_id="ca_test2",
        query=q,
        estimates=linear,
        refutations=[],
        challenges=[],
        confidence=score_confidence(linear, [], []),
        schema_version="v1",
        dataset_fingerprint="c" * 64,
        audit_record_hash="d" * 64,
    )

    view_with_forest = render(art_with_forest, "operator")
    view_linear_only = render(art_linear_only, "operator")

    assert "cate_distribution_summary" in view_with_forest
    summary = view_with_forest["cate_distribution_summary"]
    assert summary["method"] == "forest_dr"
    assert len(summary["quantiles"]) == 10
    assert summary["heterogeneity"] in {"low", "moderate", "high"}
    assert "idr" in summary

    assert "cate_distribution_summary" not in view_linear_only


# ── No-econml graceful failure ───────────────────────────────────────

@pytest.mark.skipif(econml_available(), reason="this test mocks the missing-econml path")
@pytest.mark.asyncio
async def test_forest_dr_without_econml_returns_structured_error():
    """The dispatcher must produce a CounterfactualEstimate with a
    structured error rather than crashing when econml isn't
    available. Symmetric with how double_ml falls back to its DoWhy
    stub — forest_dr just doesn't have a DoWhy equivalent so it
    surfaces the gap."""
    df = pd.DataFrame({"treatment": [0, 1, 0, 1], "outcome": [1.0, 2.0, 1.5, 2.5]})
    treatment_spec = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome_spec = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))

    estimates = await run_estimators(
        df, treatment_spec, outcome_spec, {"edges": [["treatment", "outcome"]]},
        methods=["forest_dr"],
    )
    assert len(estimates) == 1
    est = estimates[0]
    assert est.method == "forest_dr"
    assert est.error is not None
    assert "econml" in est.error.lower()
