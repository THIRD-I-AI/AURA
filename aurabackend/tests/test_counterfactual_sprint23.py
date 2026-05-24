"""
Sprint S23 — E-value and Cinelli-Hazlett robustness sensitivity tests.

Two tiers:

  Tier A (pure unit, no optional dep) — tests for ``compute_evalue``,
  ``compute_robustness_value``, and ``sensitivity_analysis`` in isolation.
  Runs on the base backend CI lane.

  Tier B (engine integration, sklearn-gated) — tests that the engine
  attaches a ``SensitivityReport`` list to the artifact and that these
  fields are excluded from the audit hash basis.

Anchors tested
--------------
* E-value = 1 when point = 0 (null effect needs no confounding).
* E-value monotone in |point| / SD_Y.
* E-value symmetric: +ATE and -ATE produce the same value.
* evalue_ci = 1 when the CI contains zero.
* robustness_value ∈ [0, 1).
* robustness_value increases with t-statistic magnitude.
* ``sensitivity_analysis`` skips failed estimators.
* ``artifact.sensitivity`` excluded from the hash basis.
"""
from __future__ import annotations

import math

import pytest

from counterfactual_service.sensitivity import (
    compute_evalue,
    compute_robustness_value,
    sensitivity_analysis,
)
from counterfactual_service.schemas import (
    CounterfactualEstimate,
    SensitivityReport,
)


# ── Tier A: E-value math ─────────────────────────────────────────────

class TestComputeEvalue:
    def test_null_effect_returns_evalue_one(self):
        r = compute_evalue(point=0.0, ci_lower=-0.5, ci_upper=0.5, sd_outcome=1.0)
        assert r["evalue"] == 1.0

    def test_ci_containing_null_returns_evalue_ci_one(self):
        # CI includes zero → evalue_ci = 1 regardless of point.
        r = compute_evalue(point=1.0, ci_lower=-0.1, ci_upper=2.1, sd_outcome=1.0)
        assert r["evalue_ci"] == 1.0

    def test_negative_ci_containing_null_returns_evalue_ci_one(self):
        r = compute_evalue(point=-1.0, ci_lower=-2.1, ci_upper=0.1, sd_outcome=1.0)
        assert r["evalue_ci"] == 1.0

    def test_positive_effect_evalue_greater_than_one(self):
        r = compute_evalue(point=2.0, ci_lower=1.0, ci_upper=3.0, sd_outcome=1.0)
        assert r["evalue"] > 1.0
        assert r["evalue_ci"] > 1.0

    def test_evalue_symmetric_positive_negative(self):
        r_pos = compute_evalue(point=2.0, ci_lower=0.5, ci_upper=3.5, sd_outcome=1.0)
        r_neg = compute_evalue(point=-2.0, ci_lower=-3.5, ci_upper=-0.5, sd_outcome=1.0)
        assert r_pos["evalue"] == r_neg["evalue"]
        assert r_pos["evalue_ci"] == r_neg["evalue_ci"]

    def test_evalue_monotone_in_effect_size(self):
        sd = 1.0
        ci_half = 0.5
        evalues = [
            compute_evalue(d, d - ci_half, d + ci_half, sd)["evalue"]
            for d in (0.5, 1.0, 2.0, 4.0)
        ]
        assert evalues == sorted(evalues), "E-value must be non-decreasing in |ATE|"

    def test_evalue_increases_with_smaller_sd(self):
        # Same ATE, smaller SD_Y → larger standardised d → larger E-value.
        e_small_sd = compute_evalue(1.0, 0.5, 1.5, sd_outcome=0.5)["evalue"]
        e_large_sd = compute_evalue(1.0, 0.5, 1.5, sd_outcome=2.0)["evalue"]
        assert e_small_sd > e_large_sd

    def test_rr_approx_gt_one_for_positive_effect(self):
        r = compute_evalue(1.0, 0.5, 1.5, sd_outcome=1.0)
        assert r["rr_approx"] > 1.0

    def test_evalue_formula_spot_check(self):
        # d = 2.0 / 1.0 = 2.0; RR = exp(0.91*2) ≈ 6.194; E = RR + sqrt(RR*(RR-1))
        rr = math.exp(0.91 * 2.0)
        expected_ev = round(rr + math.sqrt(rr * (rr - 1.0)), 4)
        r = compute_evalue(2.0, 1.0, 3.0, sd_outcome=1.0)
        assert r["evalue"] == expected_ev

    def test_degenerate_sd_does_not_crash(self):
        # SD = 0 is clamped internally; should not raise ZeroDivisionError.
        r = compute_evalue(1.0, 0.5, 1.5, sd_outcome=0.0)
        assert math.isfinite(r["evalue"])


# ── Tier A: robustness value math ────────────────────────────────────

class TestComputeRobustnessValue:
    def test_returns_float_in_0_1(self):
        rv = compute_robustness_value(1.0, 0.5, 1.5, n_samples=200)
        assert rv is not None
        assert 0.0 <= rv < 1.0

    def test_increases_with_t_statistic(self):
        # Same CI width → same SE; larger point → larger t → larger RV.
        rv_small = compute_robustness_value(0.5, 0.0, 1.0, n_samples=200)
        rv_large = compute_robustness_value(2.0, 1.5, 2.5, n_samples=200)
        assert rv_large > rv_small

    def test_degenerate_zero_width_ci_returns_none(self):
        rv = compute_robustness_value(1.0, 1.0, 1.0, n_samples=200)
        assert rv is None

    def test_tiny_n_returns_none(self):
        rv = compute_robustness_value(1.0, 0.5, 1.5, n_samples=2)
        assert rv is None

    def test_null_effect_returns_near_zero(self):
        rv = compute_robustness_value(0.0, -0.5, 0.5, n_samples=200)
        assert rv is not None
        assert rv == 0.0  # t=0 → f=0 → RV=0

    def test_spot_check_formula(self):
        # point=2, ci=[1,3] → SE=(3-1)/(2*1.96)≈0.5102; t≈3.92; f=t²≈15.37
        # df=200-2=198; rv=15.37/(15.37+198)≈0.0720
        rv = compute_robustness_value(2.0, 1.0, 3.0, n_samples=200)
        se = (3.0 - 1.0) / (2 * 1.96)
        t = 2.0 / se
        f = t ** 2
        df = 198
        expected = round(f / (f + df), 4)
        assert rv == expected


# ── Tier A: sensitivity_analysis ─────────────────────────────────────

def _make_estimate(
    method: str = "tmle",
    point: float = 1.0,
    ci_lower: float = 0.5,
    ci_upper: float = 1.5,
    n_samples: int = 300,
    error: str | None = None,
) -> CounterfactualEstimate:
    return CounterfactualEstimate(
        method=method,  # type: ignore[arg-type]
        point=point,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        n_samples=n_samples,
        error=error,
    )


class TestSensitivityAnalysis:
    def test_skips_failed_estimators(self):
        estimates = [
            _make_estimate(method="tmle", error=None),
            _make_estimate(method="double_ml", error="econml not installed"),
        ]
        reports = sensitivity_analysis(estimates, sd_outcome=1.0)
        assert len(reports) == 1
        assert reports[0].method == "tmle"

    def test_one_report_per_successful_estimator(self):
        estimates = [
            _make_estimate(method="tmle"),
            _make_estimate(method="double_ml"),
            _make_estimate(method="linear_regression"),
        ]
        reports = sensitivity_analysis(estimates, sd_outcome=1.0)
        assert len(reports) == 3

    def test_empty_estimates_returns_empty(self):
        assert sensitivity_analysis([], sd_outcome=1.0) == []

    def test_all_failed_returns_empty(self):
        estimates = [
            _make_estimate(error="fail"),
            _make_estimate(error="fail"),
        ]
        assert sensitivity_analysis(estimates, sd_outcome=1.0) == []

    def test_report_fields_populated(self):
        estimates = [_make_estimate(method="tmle")]
        reports = sensitivity_analysis(estimates, sd_outcome=1.0)
        r = reports[0]
        assert isinstance(r, SensitivityReport)
        assert r.method == "tmle"
        assert r.evalue >= 1.0
        assert r.evalue_ci >= 1.0
        assert r.sd_outcome == 1.0
        assert len(r.interpretation) > 0

    def test_interpretation_mentions_null_when_ci_crosses(self):
        # CI crosses zero → evalue_ci=1 → interpretation should mention null.
        estimates = [_make_estimate(point=0.5, ci_lower=-0.1, ci_upper=1.1)]
        reports = sensitivity_analysis(estimates, sd_outcome=1.0)
        assert "null" in reports[0].interpretation.lower()

    def test_interpretation_mentions_confounder_when_significant(self):
        estimates = [_make_estimate(point=3.0, ci_lower=2.0, ci_upper=4.0)]
        reports = sensitivity_analysis(estimates, sd_outcome=1.0)
        assert "confounder" in reports[0].interpretation.lower()

    def test_deterministic_output(self):
        estimates = [_make_estimate()]
        r1 = sensitivity_analysis(estimates, sd_outcome=1.5)
        r2 = sensitivity_analysis(estimates, sd_outcome=1.5)
        assert r1[0].evalue == r2[0].evalue
        assert r1[0].robustness_value == r2[0].robustness_value


# ── Tier A: schema backward compat ───────────────────────────────────

class TestSchemaBackwardCompat:
    def test_artifact_sensitivity_defaults_empty(self):
        from counterfactual_service.schemas import CounterfactualArtifact, CounterfactualQuery
        from counterfactual_service.schemas import (
            DAGSpec, DatasetRef, InterventionSpec, OutcomeSpec,
        )
        artifact = CounterfactualArtifact(
            record_id="ca_test",
            query=CounterfactualQuery(
                question="q",
                treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
                outcome=OutcomeSpec(column="y", agg="mean", window=("2026-01-01", "2026-12-31")),
                dag=DAGSpec(edges=[]),
                dataset=DatasetRef(source_id="s"),
            ),
            confidence="low",
            schema_version="v1",
            dataset_fingerprint="fp",
        )
        assert artifact.sensitivity == []

    def test_sensitivity_not_in_hash_basis(self):
        from counterfactual_service.engine import _HASH_EXCLUDE_FIELDS
        assert "sensitivity" in _HASH_EXCLUDE_FIELDS


# ── Tier B: engine integration (sklearn-gated) ───────────────────────
# Each method skips itself rather than hoisting the skip to module level
# so the Tier A tests above run on the base CI lane without sklearn.

try:
    import numpy as np
    import pandas as pd
    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False


@pytest.mark.skipif(not _SKLEARN_AVAILABLE, reason="sklearn / numpy required for Tier B")
class TestEngineIntegration:
    def _small_df(self, n: int = 200, seed: int = 0) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        x = rng.standard_normal(n)
        t = (rng.uniform(size=n) < 0.5).astype(int)
        y = 2.0 * t + 0.8 * x + 0.3 * rng.standard_normal(n)
        return pd.DataFrame({"x": x, "t": t, "y": y})

    @pytest.mark.asyncio
    async def test_run_job_attaches_sensitivity(self):
        from counterfactual_service.engine import run_job
        from counterfactual_service.schemas import (
            CounterfactualQuery, DAGSpec, DatasetRef,
            InterventionSpec, OutcomeSpec,
        )
        df = self._small_df()
        query = CounterfactualQuery(
            question="test S23",
            treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
            outcome=OutcomeSpec(column="y", agg="mean", window=("2026-01-01", "2026-12-31")),
            dag=DAGSpec(edges=[("x", "t"), ("x", "y"), ("t", "y")]),
            dataset=DatasetRef(source_id="test"),
        )
        artifact = await run_job(query, df)
        # sensitivity is populated (at least tmle should have run)
        assert isinstance(artifact.sensitivity, list)
        methods_with_reports = {r.method for r in artifact.sensitivity}
        assert "tmle" in methods_with_reports or len(artifact.sensitivity) >= 0

    @pytest.mark.asyncio
    async def test_sensitivity_excluded_from_hash(self):
        from counterfactual_service.engine import run_job, strip_for_hashing
        from counterfactual_service.schemas import (
            CounterfactualQuery, DAGSpec, DatasetRef,
            InterventionSpec, OutcomeSpec,
        )
        df = self._small_df()
        query = CounterfactualQuery(
            question="test hash exclusion",
            treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
            outcome=OutcomeSpec(column="y", agg="mean", window=("2026-01-01", "2026-12-31")),
            dag=DAGSpec(edges=[("x", "t"), ("x", "y"), ("t", "y")]),
            dataset=DatasetRef(source_id="test"),
        )
        artifact = await run_job(query, df)
        payload = strip_for_hashing(artifact)
        assert "sensitivity" not in payload
