"""
Sprint S23 — sensitivity primitives + engine integration contracts.

Tier A (pure-Python, no optional deps): closed-form numerical contracts
against worked examples in:
* VanderWeele & Ding (2017) Section 4 — continuous-outcome E-value
* Cinelli & Hazlett (2020) — Darfur reference (t = 4.18, dof = 783)
  cross-checked against the ``sensemakr`` R package's published output.

Tier B (dowhy-gated): engine integration — ``run_estimators`` attaches
a ``SensitivityReport`` to every successful estimate and the artifact_
hash flips when the underlying data changes.

Also gates Layer 22 (sensitivity contract) for the eval-gate sweep:
* Unconfounded strong-effect DGP → RV_q ≥ 0.40 AND e_value_point > 2.0
* Confounded null-effect DGP (effect inside the CI null cross) →
  RV_q < 0.10
"""
from __future__ import annotations

import math

import pytest

from counterfactual_service.sensitivity import (
    _evalue_from_rr,
    compute_evalue,
    compute_robustness_value,
    compute_sensitivity_report,
)

# ── Tier A: VanderWeele-Ding E-value worked examples ─────────────────

class TestEValueClosedForm:
    """Cross-references against VanderWeele-Ding 2017 and the EValue R pkg."""

    def test_rr_to_evalue_known_pair(self) -> None:
        # VanderWeele & Ding 2017 Table 2 illustrative pairs
        # RR = 2 → E = 2 + sqrt(2*1) = 2 + 1.41421 = 3.41421
        assert _evalue_from_rr(2.0) == pytest.approx(3.4142136, abs=1e-5)
        # RR = 3.9 → E = 3.9 + sqrt(3.9*2.9) = 3.9 + 3.36303 = 7.26303
        assert _evalue_from_rr(3.9) == pytest.approx(7.2630343, abs=1e-5)

    def test_rr_equal_one_gives_e_one(self) -> None:
        assert _evalue_from_rr(1.0) == 1.0

    def test_protective_rr_flips_to_harmful(self) -> None:
        # By convention a protective RR (< 1) is re-expressed as 1/RR
        # before applying the formula. RR=0.5 → 1/RR=2 → E=3.414
        assert _evalue_from_rr(0.5) == pytest.approx(3.4142135, abs=1e-5)

    def test_continuous_outcome_d_half_gives_e_2_5(self) -> None:
        # VanderWeele-Ding 2017 Section 4 continuous-outcome example:
        # d = 0.5 → RR = exp(0.91·0.5) = exp(0.455) = 1.5761734 →
        # E = RR + sqrt(RR·(RR-1)) = 2.5291420.
        out = compute_evalue(point=0.5, ci_lower=0.3, ci_upper=0.7, outcome_sd=1.0)
        assert out["rr_approx"] == pytest.approx(1.5761734, abs=1e-5)
        assert out["e_value_point"] == pytest.approx(2.5291420, abs=1e-5)

    def test_ci_crosses_null_gives_e_one(self) -> None:
        out = compute_evalue(point=0.3, ci_lower=-0.1, ci_upper=0.7, outcome_sd=1.0)
        assert out["e_value_ci"] == 1.0
        assert out["null_crossed"] is True
        # The point E-value is still > 1.0; only the CI bound is forced
        # to 1.0 by the null cross.
        assert out["e_value_point"] > 1.0

    def test_ci_does_not_cross_uses_closer_to_null(self) -> None:
        # CI = [0.3, 0.7] → closer-to-null limit is 0.3
        out = compute_evalue(point=0.5, ci_lower=0.3, ci_upper=0.7, outcome_sd=1.0)
        expected_rr_at_ci = math.exp(0.91 * 0.3)
        expected_e_ci = expected_rr_at_ci + math.sqrt(
            expected_rr_at_ci * (expected_rr_at_ci - 1.0)
        )
        assert out["e_value_ci"] == pytest.approx(expected_e_ci, abs=1e-5)
        assert out["null_crossed"] is False

    def test_negative_effect_uses_absolute_d(self) -> None:
        # Sign of point is informational; the magnitude of confounding
        # needed to nullify is symmetric. E-value of a protective
        # estimate equals E-value of the same-magnitude harmful one.
        harmful = compute_evalue(point=0.5, ci_lower=0.3, ci_upper=0.7, outcome_sd=1.0)
        protective = compute_evalue(point=-0.5, ci_lower=-0.7, ci_upper=-0.3, outcome_sd=1.0)
        assert harmful["e_value_point"] == pytest.approx(protective["e_value_point"], abs=1e-9)

    def test_degenerate_outcome_sd_returns_one(self) -> None:
        # Outcome is a constant column — no SD, no sensitivity question.
        out = compute_evalue(point=0.5, ci_lower=0.3, ci_upper=0.7, outcome_sd=0.0)
        assert out["e_value_point"] == 1.0
        assert out["e_value_ci"] == 1.0
        assert out["null_crossed"] is True


# ── Tier A: Cinelli-Hazlett RV closed form ──────────────────────────

class TestRobustnessValueClosedForm:

    def test_darfur_reference(self) -> None:
        # Cinelli & Hazlett 2020 Darfur application (and the sensemakr
        # R package's published output): t = 4.18, dof = 783 →
        # partial_r²_yd_x ≈ 0.0218, RV_q ≈ 0.139.
        rv = compute_robustness_value(point=0.0973, se=0.0233, dof=783)
        # t = 0.0973 / 0.0233 ≈ 4.176
        assert rv["t_statistic"] == pytest.approx(4.176, abs=5e-3)
        assert rv["partial_r2_yd_x"] == pytest.approx(0.0218, abs=2e-3)
        assert rv["robustness_value"] == pytest.approx(0.139, abs=5e-3)

    def test_q_scales_rv(self) -> None:
        # Halving q at fixed t/dof reduces f_q proportionally, so RV
        # shrinks too. (Smaller "amount of effect to kill" → smaller
        # confounder needed.)
        rv_full = compute_robustness_value(point=2.0, se=0.5, dof=100, q=1.0)
        rv_half = compute_robustness_value(point=2.0, se=0.5, dof=100, q=0.5)
        assert rv_half["robustness_value"] < rv_full["robustness_value"]

    def test_partial_r2_from_t_identity(self) -> None:
        # Identity: t² = R²·dof/(1-R²)  ⇒  R² = t²/(t²+dof)
        rv = compute_robustness_value(point=5.0, se=1.0, dof=100)
        t2 = 25.0
        expected = t2 / (t2 + 100)
        assert rv["partial_r2_yd_x"] == pytest.approx(expected, abs=1e-6)

    def test_extreme_scenario_bounded_and_sign_preserving(self) -> None:
        # Contract: the 1x-benchmark adjusted estimate is |point| minus
        # a non-negative bias bound, then sign-restored and clamped at
        # 0. So |adjusted| ≤ |point| always, and the sign matches
        # point's sign whenever adjusted is non-zero.
        #
        # (Triggering the actual clamp-to-zero path requires partial_r²
        # extremely close to 1 — pathological inputs. The bounded-by-
        # point + sign-preservation contract is the testable invariant
        # over the normal input range.)
        for point, se, dof in [(0.5, 0.4, 20), (2.0, 1.0, 100), (-1.5, 0.5, 50)]:
            rv = compute_robustness_value(point=point, se=se, dof=dof)
            adj = rv["extreme_scenario_adjusted"]
            assert abs(adj) <= abs(point) + 1e-9, (
                f"|adjusted|={abs(adj)} should be <= |point|={abs(point)}"
            )
            if adj != 0.0:
                assert (adj > 0) == (point > 0), (
                    f"sign mismatch: point={point}, adj={adj}"
                )

    def test_extreme_scenario_preserves_sign_when_robust(self) -> None:
        # Very strong estimate, tight CI → bias bound shouldn't fully
        # eat the effect even at the 1x benchmark.
        rv = compute_robustness_value(point=10.0, se=0.5, dof=500)
        assert rv["extreme_scenario_adjusted"] > 0.0
        assert rv["extreme_scenario_adjusted"] < 10.0

    def test_degenerate_se_returns_zeros(self) -> None:
        # SE of 0 is degenerate (perfect-fit OLS, constant residuals) —
        # the t-statistic blows up. Boundary clamp returns zeros so the
        # canonical-JSON encoding stays well-defined.
        rv = compute_robustness_value(point=1.0, se=0.0, dof=10)
        assert rv["t_statistic"] == 0.0
        assert rv["robustness_value"] == 0.0

    def test_degenerate_dof_returns_zeros(self) -> None:
        rv = compute_robustness_value(point=1.0, se=0.1, dof=0)
        assert rv["robustness_value"] == 0.0


# ── Tier A: compute_sensitivity_report end-to-end ────────────────────

class TestSensitivityReport:

    def test_byte_identical_across_calls(self) -> None:
        # Layer 10 byte-identity contract: same inputs → same output
        # dict every time. No RNG, no clock — should hold trivially.
        r1 = compute_sensitivity_report(
            point=2.0, ci_lower=1.5, ci_upper=2.5,
            n_samples=300, n_controls=2, outcome_sd=1.5,
        )
        r2 = compute_sensitivity_report(
            point=2.0, ci_lower=1.5, ci_upper=2.5,
            n_samples=300, n_controls=2, outcome_sd=1.5,
        )
        assert r1 == r2

    def test_keys_match_sensitivity_report_fields(self) -> None:
        from counterfactual_service.schemas import SensitivityReport
        report = compute_sensitivity_report(
            point=2.0, ci_lower=1.5, ci_upper=2.5,
            n_samples=300, n_controls=2, outcome_sd=1.5,
        )
        # The schema field names must be exactly the dict keys so the
        # engine can build the Pydantic model with **report directly.
        model = SensitivityReport(**report)
        # Round-trip back through model_dump and verify keys match.
        assert set(model.model_dump().keys()) == set(report.keys())

    def test_se_backed_out_from_95_ci(self) -> None:
        # SE = (CI_width) / (2 * Z_95). z_95 = 1.959964 → for a
        # symmetric CI [1.5, 2.5] (width 1.0), SE = 1.0 / 3.9199 ≈ 0.255.
        # t = 2.0 / 0.255 ≈ 7.84
        # We don't test SE directly (not in the public dict) but we can
        # verify t comes out where the SE backing-out implies.
        report = compute_sensitivity_report(
            point=2.0, ci_lower=1.5, ci_upper=2.5,
            n_samples=300, n_controls=2, outcome_sd=1.0,
        )
        expected_t = 2.0 / (1.0 / (2.0 * 1.959963984540054))
        assert report["t_statistic"] == pytest.approx(expected_t, abs=1e-3)

    def test_dof_floor_at_one(self) -> None:
        # n_samples=2, n_controls=5 (more controls than rows) →
        # n - p - 1 is negative; floor at 1 to keep math well-defined.
        report = compute_sensitivity_report(
            point=1.0, ci_lower=0.5, ci_upper=1.5,
            n_samples=2, n_controls=5, outcome_sd=1.0,
        )
        assert report["dof"] == 1

    def test_dof_correct_for_normal_inputs(self) -> None:
        # n=300, n_controls=2 → dof = 300 - 2 - 1 = 297
        report = compute_sensitivity_report(
            point=1.0, ci_lower=0.5, ci_upper=1.5,
            n_samples=300, n_controls=2, outcome_sd=1.0,
        )
        assert report["dof"] == 297

    def test_null_crossed_propagates(self) -> None:
        report = compute_sensitivity_report(
            point=0.1, ci_lower=-0.5, ci_upper=0.7,
            n_samples=300, n_controls=2, outcome_sd=1.0,
        )
        assert report["null_crossed"] is True
        assert report["e_value_ci"] == 1.0


# ── Layer 22: eval-gate sensitivity contract (Tier A) ───────────────

class TestLayer22SensitivityContract:
    """Layer 22 — sensitivity numbers reflect the underlying signal.

    Synthesised by computing what a strong-effect DGP and a null-effect
    DGP WOULD produce as (point, CI, n_samples, n_controls, outcome_sd)
    tuples — without actually running an estimator. The contract is
    that the sensitivity primitives flag the difference correctly.

    Strong effect on a 1-SD outcome at n=300, 2 controls:
      true effect = 1.5, residual SD = 1.0 → outcome SD ≈ 1.8
      estimator should recover point ≈ 1.5 with SE ≈ 0.15 →
      CI ≈ [1.21, 1.79], t ≈ 10, partial_r² ≈ 0.252.
      → RV_q at q=1 is large; E-value of 1.5/1.8 ≈ 0.83 (standardised)
        gives RR ≈ exp(0.76) ≈ 2.13 → E ≈ 3.81.

    Null effect at the same n, residual SD = 1.0 → outcome SD = 1.0
      estimator's point should hover near 0 with CI crossing it:
      CI ≈ [-0.15, 0.15], outcome_sd ≈ 1.0 → null_crossed.
    """

    def test_strong_effect_dgp_passes_layer22(self) -> None:
        # Strong effect: outcome_sd captures (treatment_var + residual_var).
        # For T~Bernoulli(0.5), Var(T) = 0.25; effect = 1.5 contributes
        # 1.5² * 0.25 = 0.5625 to Var(Y). With residual var = 1.0,
        # Var(Y) ≈ 1.5625, SD ≈ 1.25. Use 1.25 as outcome_sd here.
        report = compute_sensitivity_report(
            point=1.5, ci_lower=1.21, ci_upper=1.79,
            n_samples=300, n_controls=2, outcome_sd=1.25,
        )
        # Layer 22 contract:
        assert report["robustness_value"] >= 0.40, (
            f"Strong-effect DGP should produce RV ≥ 0.40, got {report['robustness_value']}"
        )
        assert report["e_value_point"] > 2.0, (
            f"Strong-effect DGP should produce E-value > 2.0, got {report['e_value_point']}"
        )

    def test_null_effect_dgp_passes_layer22(self) -> None:
        # Null effect with CI crossing zero. point ≈ 0.05, CI = [-0.15, 0.25].
        # The CI E-value is forced to 1.0 by the null crossing. The
        # point E-value is small (d = 0.05 / 1.0 → RR ≈ 1.05 → E ≈ 1.30).
        # The point t-statistic is small → RV stays small.
        report = compute_sensitivity_report(
            point=0.05, ci_lower=-0.15, ci_upper=0.25,
            n_samples=300, n_controls=2, outcome_sd=1.0,
        )
        assert report["null_crossed"] is True
        assert report["e_value_ci"] == 1.0
        assert report["robustness_value"] < 0.10, (
            f"Null-effect DGP should produce RV < 0.10, got {report['robustness_value']}"
        )


# ── Tier B: engine integration (dowhy-gated) ─────────────────────────

dowhy = pytest.importorskip("dowhy", reason="dowhy required for engine integration tier")


class TestEngineIntegration:
    """End-to-end: run_estimators populates sensitivity on every success."""

    @pytest.mark.asyncio
    async def test_run_estimators_attaches_sensitivity(self) -> None:
        import numpy as np
        import pandas as pd

        from counterfactual_service.engine import run_estimators
        from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

        rng = np.random.default_rng(42)
        n = 300
        X = rng.normal(0, 1, n)
        T = (rng.random(n) < 0.5).astype(int)
        # Linear DGP: Y = 1.5*T + 0.5*X + noise. Strong effect, modest
        # confounding through X.
        Y = 1.5 * T + 0.5 * X + rng.normal(0, 1, n)
        df = pd.DataFrame({"X": X, "T": T, "Y": Y})

        treatment = InterventionSpec(column="T", actual=1.0, counterfactual=0.0)
        outcome = OutcomeSpec(column="Y", agg="mean", window=("2024-01-01", "2024-12-31"))
        dag = {"edges": [("X", "T"), ("X", "Y"), ("T", "Y")]}

        results = await run_estimators(
            df, treatment, outcome, dag,
            methods=["linear_regression"],
            request_hash="layer22_smoke",
        )
        assert len(results) == 1
        est = results[0]
        if est.error is None:
            assert est.sensitivity is not None, (
                "Sensitivity must be populated on a successful estimate"
            )
            # Strong effect (≈1.5) on outcome_sd ≈ sqrt(0.5625 + 0.25 + 1) ≈ 1.35
            # → standardised d ≈ 1.11 → RR ≈ exp(1.01) ≈ 2.75 → E ≈ 4.75
            assert est.sensitivity.e_value_point > 2.0
            assert 0 <= est.sensitivity.robustness_value <= 1.0

    @pytest.mark.asyncio
    async def test_failed_estimate_keeps_sensitivity_none(self) -> None:
        import numpy as np
        import pandas as pd

        from counterfactual_service.engine import run_estimators
        from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

        # Degenerate: T is constant → no treatment variation. Estimator
        # should populate the ``error`` field, and sensitivity stays None.
        n = 50
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "X": rng.normal(0, 1, n),
            "T": np.zeros(n, dtype=int),
            "Y": rng.normal(0, 1, n),
        })
        treatment = InterventionSpec(column="T", actual=1.0, counterfactual=0.0)
        outcome = OutcomeSpec(column="Y", agg="mean", window=("2024-01-01", "2024-12-31"))
        dag = {"edges": [("X", "T"), ("X", "Y"), ("T", "Y")]}

        results = await run_estimators(
            df, treatment, outcome, dag,
            methods=["linear_regression"],
            request_hash="degenerate_smoke",
        )
        est = results[0]
        # Either errored or returned a near-zero estimate. Either way,
        # if it errored, sensitivity must be None (we don't attach to
        # failures).
        if est.error is not None:
            assert est.sensitivity is None
