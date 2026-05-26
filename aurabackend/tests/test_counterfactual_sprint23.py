"""
Sprint S23 — E-value and Cinelli-Hazlett robustness sensitivity tests.

Two tiers:

  Tier A (pure unit, no optional dep) — tests for ``compute_evalue``,
  ``compute_robustness_value``, and ``compute_sensitivity_report`` in
  isolation.  Runs on the base backend CI lane.

  Tier B (engine integration, numpy/pandas-gated) — tests that the engine
  attaches ``SensitivityReport`` to each successful ``CounterfactualEstimate``
  and that the field IS in the artifact hash basis (not excluded).

Anchors tested
--------------
* E-value = 1 when point = 0 (null effect needs no confounding).
* E-value monotone in |point| / outcome_sd.
* E-value symmetric: +ATE and -ATE produce the same value.
* e_value_ci = 1 when the CI contains zero (null_crossed=True).
* robustness_value ∈ [0, 1).
* robustness_value increases with t-statistic magnitude.
* compute_sensitivity_report keys match SensitivityReport field names.
* artifact.estimates[i].sensitivity populated for successful estimates.
* "sensitivity" is NOT in _HASH_EXCLUDE_FIELDS (it is IN the hash basis).
"""
from __future__ import annotations

import math

import pytest

from counterfactual_service.schemas import (
    CounterfactualEstimate,
    SensitivityReport,
)
from counterfactual_service.sensitivity import (
    compute_evalue,
    compute_robustness_value,
    compute_sensitivity_report,
)

# ── Tier A: compute_evalue ───────────────────────────────────────────

class TestComputeEvalue:
    def test_null_effect_returns_evalue_one(self):
        r = compute_evalue(point=0.0, ci_lower=-0.5, ci_upper=0.5, outcome_sd=1.0)
        assert r["e_value_point"] == 1.0

    def test_ci_containing_null_returns_evalue_ci_one(self):
        r = compute_evalue(point=1.0, ci_lower=-0.1, ci_upper=2.1, outcome_sd=1.0)
        assert r["e_value_ci"] == 1.0
        assert r["null_crossed"] is True

    def test_negative_ci_containing_null_returns_evalue_ci_one(self):
        r = compute_evalue(point=-1.0, ci_lower=-2.1, ci_upper=0.1, outcome_sd=1.0)
        assert r["e_value_ci"] == 1.0
        assert r["null_crossed"] is True

    def test_positive_effect_both_evalues_gt_one(self):
        r = compute_evalue(point=2.0, ci_lower=1.0, ci_upper=3.0, outcome_sd=1.0)
        assert r["e_value_point"] > 1.0
        assert r["e_value_ci"] > 1.0
        assert r["null_crossed"] is False

    def test_evalue_symmetric_positive_negative(self):
        r_pos = compute_evalue(point=2.0, ci_lower=0.5, ci_upper=3.5, outcome_sd=1.0)
        r_neg = compute_evalue(point=-2.0, ci_lower=-3.5, ci_upper=-0.5, outcome_sd=1.0)
        assert r_pos["e_value_point"] == r_neg["e_value_point"]
        assert r_pos["e_value_ci"] == r_neg["e_value_ci"]

    def test_evalue_monotone_in_effect_size(self):
        ci_half = 0.5
        evalues = [
            compute_evalue(d, d - ci_half, d + ci_half, outcome_sd=1.0)["e_value_point"]
            for d in (0.5, 1.0, 2.0, 4.0)
        ]
        assert evalues == sorted(evalues), "E-value must be non-decreasing in |ATE|"

    def test_evalue_increases_with_smaller_outcome_sd(self):
        e_small = compute_evalue(1.0, 0.5, 1.5, outcome_sd=0.5)["e_value_point"]
        e_large = compute_evalue(1.0, 0.5, 1.5, outcome_sd=2.0)["e_value_point"]
        assert e_small > e_large

    def test_rr_approx_gt_one_for_positive_effect(self):
        r = compute_evalue(1.0, 0.5, 1.5, outcome_sd=1.0)
        assert r["rr_approx"] > 1.0

    def test_standardised_effect_d_sign(self):
        r = compute_evalue(2.0, 1.0, 3.0, outcome_sd=1.0)
        assert r["standardised_effect_d"] == pytest.approx(2.0, abs=1e-5)

    def test_evalue_formula_spot_check(self):
        # d = 2.0/1.0 = 2.0; RR = exp(0.91*2); E = RR + sqrt(RR*(RR-1))
        rr = math.exp(0.91 * 2.0)
        expected = round(rr + math.sqrt(rr * (rr - 1.0)), 6)
        r = compute_evalue(2.0, 1.0, 3.0, outcome_sd=1.0)
        assert r["e_value_point"] == expected

    def test_degenerate_sd_does_not_crash(self):
        r = compute_evalue(1.0, 0.5, 1.5, outcome_sd=0.0)
        assert math.isfinite(r["e_value_point"])
        assert r["e_value_point"] == 1.0  # sentinel: "no information"

    def test_all_keys_present(self):
        r = compute_evalue(1.0, 0.5, 1.5, outcome_sd=1.0)
        assert set(r.keys()) >= {
            "e_value_point", "e_value_ci", "rr_approx",
            "standardised_effect_d", "null_crossed",
        }


# ── Tier A: compute_robustness_value ────────────────────────────────

class TestComputeRobustnessValue:
    def test_returns_dict_with_required_keys(self):
        r = compute_robustness_value(point=2.0, se=0.5, dof=198)
        assert set(r.keys()) >= {
            "t_statistic", "dof", "partial_r2_yd_x",
            "robustness_value", "extreme_scenario_adjusted",
        }

    def test_robustness_value_in_0_1(self):
        r = compute_robustness_value(point=2.0, se=0.5, dof=198)
        assert 0.0 <= r["robustness_value"] < 1.0

    def test_t_statistic_is_point_over_se(self):
        r = compute_robustness_value(point=3.0, se=0.6, dof=100)
        assert r["t_statistic"] == pytest.approx(5.0, abs=1e-5)

    def test_partial_r2_formula(self):
        # partial_r2 = t² / (t² + dof)
        r = compute_robustness_value(point=4.0, se=1.0, dof=100)
        t2 = 16.0
        expected = round(t2 / (t2 + 100), 6)
        assert r["partial_r2_yd_x"] == expected

    def test_robustness_value_increases_with_t_statistic(self):
        rv_small = compute_robustness_value(point=0.5, se=0.5, dof=200)["robustness_value"]
        rv_large = compute_robustness_value(point=4.0, se=0.5, dof=200)["robustness_value"]
        assert rv_large > rv_small

    def test_zero_point_gives_rv_zero(self):
        r = compute_robustness_value(point=0.0, se=0.5, dof=100)
        assert r["robustness_value"] == 0.0

    def test_degenerate_se_returns_guard_output(self):
        r = compute_robustness_value(point=1.0, se=0.0, dof=100)
        assert r["t_statistic"] == 0.0
        assert r["robustness_value"] == 0.0

    def test_extreme_scenario_adjusted_at_most_abs_point(self):
        r = compute_robustness_value(point=2.0, se=0.5, dof=198)
        assert abs(r["extreme_scenario_adjusted"]) <= abs(2.0)

    def test_extreme_scenario_adjusted_finite(self):
        r = compute_robustness_value(point=2.0, se=0.5, dof=198)
        assert math.isfinite(r["extreme_scenario_adjusted"])


# ── Tier A: compute_sensitivity_report ──────────────────────────────

class TestComputeSensitivityReport:
    def test_keys_match_sensitivity_report_fields(self):
        schema_fields = set(SensitivityReport.model_fields.keys())
        r = compute_sensitivity_report(
            point=1.0, ci_lower=0.5, ci_upper=1.5,
            n_samples=200, n_controls=2, outcome_sd=1.0,
        )
        assert set(r.keys()) == schema_fields

    def test_populates_e_value_and_robustness(self):
        r = compute_sensitivity_report(
            point=2.0, ci_lower=1.0, ci_upper=3.0,
            n_samples=300, n_controls=3, outcome_sd=1.0,
        )
        assert math.isfinite(r["e_value_point"])
        assert math.isfinite(r["robustness_value"])

    def test_deterministic(self):
        kwargs = dict(
            point=1.5, ci_lower=0.8, ci_upper=2.2,
            n_samples=150, n_controls=2, outcome_sd=0.9,
        )
        r1 = compute_sensitivity_report(**kwargs)
        r2 = compute_sensitivity_report(**kwargs)
        assert r1 == r2

    def test_null_crossed_propagated(self):
        r = compute_sensitivity_report(
            point=0.5, ci_lower=-0.2, ci_upper=1.2,
            n_samples=200, n_controls=1, outcome_sd=1.0,
        )
        assert r["null_crossed"] is True
        assert r["e_value_ci"] == 1.0

    def test_constructs_valid_sensitivity_report(self):
        r = compute_sensitivity_report(
            point=2.0, ci_lower=1.0, ci_upper=3.0,
            n_samples=250, n_controls=2, outcome_sd=1.0,
        )
        report = SensitivityReport(**r)
        assert report.e_value_point >= 1.0
        assert 0.0 <= report.robustness_value < 1.0


# ── Tier A: schema tests ─────────────────────────────────────────────

class TestSchemaContracts:
    def test_estimate_sensitivity_defaults_none(self):
        est = CounterfactualEstimate(
            method="linear_regression",
            point=1.0,
            ci_lower=0.5,
            ci_upper=1.5,
            n_samples=100,
        )
        assert est.sensitivity is None

    def test_sensitivity_not_in_hash_exclude_fields(self):
        from counterfactual_service.engine import _HASH_EXCLUDE_FIELDS
        assert "sensitivity" not in _HASH_EXCLUDE_FIELDS


# ── Tier B: engine integration (numpy/pandas-gated) ──────────────────

try:
    import numpy as np
    import pandas as pd
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

try:
    from counterfactual_service.engine import dowhy_available as _dowhy_available
    _DOWHY_AVAILABLE = _dowhy_available()
except Exception:
    _DOWHY_AVAILABLE = False

_TIER_B_AVAILABLE = _NUMPY_AVAILABLE and _DOWHY_AVAILABLE


def _small_df(n: int = 200, seed: int = 0) -> "pd.DataFrame":
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    t = (rng.uniform(size=n) < 0.5).astype(int)
    y = 2.0 * t + 0.8 * x + 0.3 * rng.standard_normal(n)
    return pd.DataFrame({"x": x, "t": t, "y": y})


def _make_query():
    from counterfactual_service.schemas import (
        CounterfactualQuery,
        DAGSpec,
        DatasetRef,
        InterventionSpec,
        OutcomeSpec,
    )
    return CounterfactualQuery(
        question="S23 integration test",
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(column="y", agg="mean", window=("2026-01-01", "2026-12-31")),
        dag=DAGSpec(edges=[("x", "t"), ("x", "y"), ("t", "y")]),
        dataset=DatasetRef(source_id="test"),
    )


@pytest.mark.skipif(
    not _TIER_B_AVAILABLE,
    reason="numpy/pandas + dowhy required for Tier B engine integration",
)
class TestEngineIntegration:
    """Full-engine integration tests.

    Each test redirects AURA_AUDIT_DIR to a tmp_path and installs the
    unified mock LLM — identical setup to test_counterfactual_engine.py
    — so the critic-cache and persistence layers never touch /var/log/aura.
    """

    def _run_job(self, monkeypatch, tmp_path):
        from counterfactual_service.engine import run_job
        from tests._mock_llm import UnifiedMockLLM, install_mock
        install_mock(monkeypatch, UnifiedMockLLM(default_response='{"challenges": []}'))
        monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))
        return run_job(_make_query(), _small_df())

    @pytest.mark.asyncio
    async def test_run_job_attaches_sensitivity_to_estimates(self, monkeypatch, tmp_path):
        artifact = await self._run_job(monkeypatch, tmp_path)
        successful = [e for e in artifact.estimates if e.error is None]
        assert len(successful) > 0, "At least one estimator must succeed"
        for est in successful:
            assert est.sensitivity is not None, (
                f"estimate {est.method} missing sensitivity"
            )
            assert isinstance(est.sensitivity, SensitivityReport)

    @pytest.mark.asyncio
    async def test_failed_estimates_have_sensitivity_none(self, monkeypatch, tmp_path):
        artifact = await self._run_job(monkeypatch, tmp_path)
        for est in artifact.estimates:
            if est.error is not None:
                assert est.sensitivity is None

    @pytest.mark.asyncio
    async def test_sensitivity_in_hash_payload(self, monkeypatch, tmp_path):
        from counterfactual_service.engine import strip_for_hashing
        artifact = await self._run_job(monkeypatch, tmp_path)
        payload = strip_for_hashing(artifact)
        estimates_in_payload = payload.get("estimates", [])
        successful_payload = [e for e in estimates_in_payload if e.get("error") is None]
        assert len(successful_payload) > 0
        for est_dict in successful_payload:
            assert "sensitivity" in est_dict, (
                "sensitivity must be in hash payload (not excluded)"
            )

    @pytest.mark.asyncio
    async def test_sensitivity_e_value_finite_and_gte_one(self, monkeypatch, tmp_path):
        artifact = await self._run_job(monkeypatch, tmp_path)
        for est in artifact.estimates:
            if est.sensitivity is not None:
                assert math.isfinite(est.sensitivity.e_value_point)
                assert est.sensitivity.e_value_point >= 1.0

    @pytest.mark.asyncio
    async def test_sensitivity_robustness_value_in_0_1(self, monkeypatch, tmp_path):
        artifact = await self._run_job(monkeypatch, tmp_path)
        for est in artifact.estimates:
            if est.sensitivity is not None:
                rv = est.sensitivity.robustness_value
                assert 0.0 <= rv < 1.0, f"rv={rv} out of [0,1) for {est.method}"
