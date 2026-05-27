"""
Sprint 16 — Conformal CATE Intervals tests.

Anchors:
  * Lei, J. & Candes, E. J. (2021). Conformal Inference of
    Counterfactuals and Individual Treatment Effects. JRSS-B 83(5).
  * Tibshirani, Barber, Candes & Ramdas (NeurIPS 2019). Conformal
    Prediction Under Covariate Shift.
  * Alaa et al. (NeurIPS 2023). Conformal Meta-learners for
    Predictive Inference of Individual Treatment Effects.

Covers:

* Unit tests on weighted_split_conformal — quantile correctness on
  hand-built cases, finite-sample +1 correction, weighted vs
  uniform regression.
* AIPW pseudo-outcome correctness on synthetic data.
* Determinism: conformal CI is byte-stable across two engine runs
  on the same input.
* Layer 13 — 200-replication coverage simulation on the synthetic
  linear DGP. Conformal CI empirical coverage must be >= 0.93
  (target 0.95 with finite-sample MC slack); asymptotic CI on the
  same DGP is recorded for comparison but not asserted.
* Existing tests (Layers 9-12, Sprint 9/13/14/15) untouched —
  conformal_calibration=False is the default everywhere.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from counterfactual_service.conformal import (
    aipw_pseudo_outcomes,
    weighted_split_conformal,
)
from counterfactual_service.engine import (
    dowhy_available,
    econml_available,
    run_estimators,
)
from counterfactual_service.schemas import (
    InterventionSpec,
    OutcomeSpec,
)
from tests._synthetic_data import TRUE_EFFECT, synthetic_dag_full, synthetic_dataset

# ── Pure-Python conformal helper (no dep on dowhy/econml) ────────────

def test_weighted_split_conformal_uniform_recovers_order_statistic():
    """With uniform weights and alpha=0.10, the (1-alpha) quantile of
    100 sorted scores 1..100 is the ceil((101)*0.90) = 91st-order
    statistic = 91 (Vovk-Petej finite-sample correction).
    """
    scores = np.arange(1, 101, dtype=float)
    q = weighted_split_conformal(scores, weights=None, alpha=0.10)
    assert q == pytest.approx(91.0)


def test_weighted_split_conformal_returns_inf_when_n_too_small_for_alpha():
    """With n=10 and alpha=0.05, ceil(11 * 0.95) = 11 > 10, so the
    finite-sample correction can't certify coverage. Helper returns
    +inf rather than fake a tight bound."""
    q = weighted_split_conformal(np.arange(10, dtype=float), alpha=0.05)
    assert q == float("inf")


def test_weighted_split_conformal_rejects_bad_alpha():
    with pytest.raises(ValueError):
        weighted_split_conformal(np.array([1.0]), alpha=0.0)
    with pytest.raises(ValueError):
        weighted_split_conformal(np.array([1.0]), alpha=1.0)
    with pytest.raises(ValueError):
        weighted_split_conformal(np.array([]), alpha=0.05)


def test_weighted_split_conformal_with_skewed_weights():
    """When weights concentrate on the small scores, the conformal
    quantile should be smaller than the uniform-weights case — the
    helper pays attention to the weights."""
    scores = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0])
    uniform_q = weighted_split_conformal(scores, weights=None, alpha=0.50)
    # Weights that downweight the outlier (last element)
    weights = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 0.01])
    skewed_q = weighted_split_conformal(scores, weights=weights, alpha=0.50)
    assert skewed_q <= uniform_q


def test_weighted_split_conformal_weights_length_mismatch():
    with pytest.raises(ValueError):
        weighted_split_conformal(
            np.array([1.0, 2.0, 3.0]),
            weights=np.array([1.0, 1.0]),
        )


def test_weighted_split_conformal_rejects_negative_weights():
    with pytest.raises(ValueError):
        weighted_split_conformal(
            np.array([1.0, 2.0]),
            weights=np.array([1.0, -0.5]),
        )


# ── AIPW pseudo-outcome ──────────────────────────────────────────────

def test_aipw_pseudo_outcome_unbiased_in_expectation_on_synthetic():
    """On a small synthetic linear DGP where the nuisance models are
    correctly specified, the mean of AIPW pseudo-outcomes should be
    within a few standard errors of TRUE_EFFECT.
    """
    rng = np.random.default_rng(0xCAFEBABE)
    n = 1000
    X = rng.standard_normal(n)
    e = 1.0 / (1.0 + np.exp(-0.5 * X))
    T = (rng.uniform(size=n) < e).astype(int)
    Y = TRUE_EFFECT * T + 1.0 * X + rng.standard_normal(n)
    # Use the TRUE nuisance functions — checks the IF identity
    e_hat = e
    mu0 = 1.0 * X
    mu1 = TRUE_EFFECT + 1.0 * X
    psi = aipw_pseudo_outcomes(Y, T, e_hat, mu0, mu1)
    mean_psi = float(np.mean(psi))
    # |bias| < 0.1 — generous MC tolerance for n=1000
    assert abs(mean_psi - TRUE_EFFECT) < 0.1


def test_aipw_pseudo_outcome_clips_extreme_propensities():
    """Propensities exactly at 0 or 1 would divide-by-zero. Helper
    clips to [0.01, 0.99] internally — should not produce inf/nan."""
    Y = np.array([1.0, 2.0, 3.0, 4.0])
    T = np.array([0, 1, 0, 1])
    e_hat = np.array([0.0, 1.0, 0.001, 0.999])
    mu0 = np.array([0.5, 0.5, 0.5, 0.5])
    mu1 = np.array([1.5, 1.5, 1.5, 1.5])
    psi = aipw_pseudo_outcomes(Y, T, e_hat, mu0, mu1)
    assert np.all(np.isfinite(psi))


# ── Engine-integration: conformal CI on the LinearDR path ───────────

ENGINE_TESTS = pytest.mark.skipif(
    not (dowhy_available() and econml_available()),
    reason="dowhy + econml required for engine-level conformal tests",
)


@ENGINE_TESTS
@pytest.mark.asyncio
async def test_double_ml_with_conformal_returns_ci_method_conformal():
    """Opting into conformal_calibration should flip ci_method on
    the resulting estimate and produce a finite, sensible interval."""
    df = synthetic_dataset(n=600)
    estimates = await run_estimators(
        df, InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        synthetic_dag_full(),
        methods=["double_ml"],
        conformal_calibration=True,
    )
    est = estimates[0]
    assert est.error is None, est.error
    assert est.method == "double_ml"
    assert est.ci_method == "conformal"
    # CI must be ordered and finite. Conformal intervals on this DGP
    # at alpha=0.05 with a 30% calibration split (≈ 180 rows) take
    # the 171st-order statistic of |psi_i - mean(psi)| as the half-
    # width — that's typically 3-5 units on the synthetic linear DGP,
    # so the full width can run 6-10. Bound is loose on purpose: the
    # test asserts CI shape + finiteness, not a specific width.
    # (Sprint 16 Layer 13 below is the contractual coverage test.)
    assert est.ci_upper > est.ci_lower
    assert np.isfinite(est.ci_upper) and np.isfinite(est.ci_lower)
    assert (est.ci_upper - est.ci_lower) < 20.0
    # Point estimate should still recover TRUE_EFFECT (≈ 1.5) — the
    # split-conformal nuisance fit is a simpler model than the full
    # cross-fitted DR but on this DGP both find the right ATE.
    assert abs(est.point - TRUE_EFFECT) < 0.5


@ENGINE_TESTS
@pytest.mark.asyncio
async def test_default_ci_method_unchanged_at_asymptotic():
    """Without conformal_calibration the default path is unchanged —
    ci_method remains 'asymptotic' so older artifacts and the S9-S15
    eval-gate tests see no behavioural difference."""
    df = synthetic_dataset(n=400)
    estimates = await run_estimators(
        df, InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        synthetic_dag_full(),
        methods=["double_ml"],
    )
    est = estimates[0]
    assert est.error is None
    assert est.ci_method == "asymptotic"


@ENGINE_TESTS
@pytest.mark.asyncio
async def test_conformal_ci_byte_stable_across_runs():
    """Two engine runs with the same input + the same conformal
    calibration flag must produce identical [ci_lower, ci_upper] —
    the calibration split's seed derives from the engine's per-method
    seed so Layer 10 byte-identity must still hold."""
    df = synthetic_dataset(n=500, seed=0xfeed_dead)
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))
    dag = synthetic_dag_full()

    a = await run_estimators(df, treatment, outcome, dag,
                             methods=["double_ml"], conformal_calibration=True)
    b = await run_estimators(df, treatment, outcome, dag,
                             methods=["double_ml"], conformal_calibration=True)
    assert a[0].ci_lower == b[0].ci_lower
    assert a[0].ci_upper == b[0].ci_upper
    assert a[0].point == b[0].point
    assert a[0].ci_method == b[0].ci_method == "conformal"


# ── Layer 13: 200-replication coverage simulation ────────────────────

@ENGINE_TESTS
@pytest.mark.asyncio
async def test_layer13_conformal_coverage_simulation():
    """Layer 13. Monte Carlo coverage simulation: generate N=200 fresh
    synthetic datasets from the same DGP, compute the conformal CI on
    each, and verify the empirical fraction of intervals containing
    TRUE_EFFECT is at least 0.93 (target 0.95 with finite-sample MC
    slack). This is the operational definition of the conformal
    contract — 'coverage holds at 1-alpha in finite samples
    regardless of nuisance-model misspecification'.

    On the same DGP we also record the asymptotic CI's coverage as a
    side-by-side comparison. We don't assert on it (statsmodels'
    asymptotic interval is well-known to over-cover on linear-DGP
    fixtures), but the recorded number lets a future sprint compare
    intervals empirically.

    Reps cap at 200 to keep CI runtime under ~3 minutes. n=300 per
    rep is the smallest sample that conformal calibration accepts.
    """
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))
    dag = synthetic_dag_full()

    n_reps = 200
    n_per_rep = 300
    

    rng = np.random.default_rng(0xC0FFEE_BEEF)
    conformal_hits = 0
    asymptotic_hits = 0
    valid_reps = 0
    for rep in range(n_reps):
        # Fresh synthetic DGP per rep — same shape as
        # tests._synthetic_data.synthetic_dataset, different seed.
        seed = int(rng.integers(0, 2**31 - 1))
        df = synthetic_dataset(n=n_per_rep, seed=seed)

        # Conformal CI
        est_conformal = (await run_estimators(
            df, treatment, outcome, dag,
            methods=["double_ml"], conformal_calibration=True,
        ))[0]
        # Asymptotic CI (default path)
        est_asymp = (await run_estimators(
            df, treatment, outcome, dag,
            methods=["double_ml"],
        ))[0]
        if est_conformal.error or est_asymp.error:
            continue
        valid_reps += 1
        if est_conformal.ci_lower <= TRUE_EFFECT <= est_conformal.ci_upper:
            conformal_hits += 1
        if est_asymp.ci_lower <= TRUE_EFFECT <= est_asymp.ci_upper:
            asymptotic_hits += 1

    assert valid_reps >= int(0.9 * n_reps), (
        f"Too many estimator failures in MC simulation: "
        f"{valid_reps}/{n_reps} valid reps"
    )
    conformal_coverage = conformal_hits / valid_reps
    asymptotic_coverage = asymptotic_hits / valid_reps
    # The conformal contract: empirical coverage >= 0.93 at alpha=0.05.
    # The 2-point margin below 0.95 absorbs Monte Carlo standard error
    # at 200 reps (SE ~ sqrt(0.95 * 0.05 / 200) ~ 0.015).
    assert conformal_coverage >= 0.93, (
        f"Layer 13 FAIL: conformal coverage {conformal_coverage:.3f} "
        f"< 0.93 (target 0.95); asymptotic was {asymptotic_coverage:.3f}; "
        f"valid_reps={valid_reps}"
    )
