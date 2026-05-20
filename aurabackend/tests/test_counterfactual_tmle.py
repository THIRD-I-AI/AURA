"""
Sprint S22 — contract tests for cross-fitted Targeted Maximum
Likelihood Estimation on the ATE.

Two tiers:

  Tier A (pure unit, no optional dep) — the ``run_tmle_ate`` math
  on a synthetic DGP. The eval-gate Layer 19 contract: recovers
  TRUE_EFFECT within MAE 0.20 (tighter than DR-Learner's 0.30
  bound because TMLE achieves the semi-parametric efficiency
  bound).

  Tier B (engine integration, dowhy-gated) — the ``_run_one_tmle``
  wrapper inside the engine fan-out. Lives in
  ``test_counterfactual_*.py`` so it picks up the eval-gate CI lane
  the same way the LinearDR / ForestDR tests do.

Why both tiers?
---------------
Tier A defends the algorithm in isolation — pure NumPy + sklearn,
runs on the base backend lane, fast (< 1s), catches algorithmic
regressions without needing dowhy/econml installed.

Tier B defends the engine wiring — that the dispatch routes "tmle"
through ``_run_one_tmle``, that the ``PropensityDiagnostics`` field
populates correctly, that the result lands in a valid
``CounterfactualEstimate`` with ci_method="asymptotic" (the
default — conformal calibration is reserved for a future S22.1).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# sklearn is NOT in aurabackend/requirements.txt — only in
# requirements-causal.txt (transitively via econml). The base backend
# CI lane does NOT install it, so importing ``counterfactual_service.tmle``
# (which imports sklearn at module load) would fail collection. Gate
# the whole module on sklearn availability.
#
# The eval-gate CI lane installs requirements-causal.txt and runs
# ``test_counterfactual_*.py`` end-to-end — that's where these tests
# actually execute. NOT a silent-skip false-green per
# [[feedback_optional_dep_test_gating]] because the eval-gate lane
# globs every counterfactual test file.
pytest.importorskip("sklearn")

from counterfactual_service.tmle import run_tmle_ate  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────


def _synthetic_linear_dgp(
    n: int = 500,
    true_ate: float = 2.0,
    n_confounders: int = 3,
    seed: int = 42,
) -> tuple:
    """Standard synthetic DGP used by the eval-gate.

    * Treatment depends on the first 2 confounders (a real propensity
      surface, not random assignment).
    * Outcome depends on the treatment + ALL confounders, with mild
      Gaussian noise.
    * True ATE = ``true_ate``; the test asserts TMLE recovers it
      within the Layer 19 MAE bound.
    """
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, n_confounders))
    logit = 0.5 * X[:, 0] - 0.3 * X[:, 1]
    p = 1.0 / (1.0 + np.exp(-logit))
    T = (rng.uniform(size=n) < p).astype(int)
    Y = (
        true_ate * T
        + 1.5 * X[:, 0]
        - 0.8 * X[:, 2]
        + 0.5 * rng.standard_normal(n)
    )
    return Y, T, X


# ── Tier A: algorithmic contracts ────────────────────────────────────


def test_tmle_recovers_true_ate_within_mae_020() -> None:
    """**Layer 19 contract**: TMLE recovers TRUE_EFFECT within MAE
    0.20 on the synthetic linear DGP. Tighter than DR-Learner's
    0.30 because TMLE achieves the semi-parametric efficiency bound
    while DR-Learner doesn't."""
    Y, T, X = _synthetic_linear_dgp(n=500, true_ate=2.0)
    result = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    error = abs(result["point"] - 2.0)
    assert error <= 0.20, (
        f"TMLE failed Layer 19 contract: point={result['point']:.3f}, "
        f"|error| = {error:.3f} exceeded MAE 0.20 bound."
    )


def test_tmle_ci_covers_truth_in_steady_state() -> None:
    """Asymptotic CI built from the efficient influence curve should
    cover the truth on a well-behaved DGP. A single-run failure isn't
    proof of mis-coverage (CIs by definition cover ~95% of the time),
    but on the chosen seed + sample size the CI should be wide enough
    to include 2.0."""
    Y, T, X = _synthetic_linear_dgp(n=500, true_ate=2.0)
    result = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    assert result["ci_lower"] <= 2.0 <= result["ci_upper"], (
        f"95% CI [{result['ci_lower']:.3f}, {result['ci_upper']:.3f}] "
        f"did not cover the truth (2.0) on the test seed."
    )


def test_tmle_deterministic_across_runs() -> None:
    """Same seed + same data → byte-identical output. Required for
    Layer 10 audit-engine byte-identity (TMLE shipping in a future
    integration must not break the artifact hash basis)."""
    Y, T, X = _synthetic_linear_dgp(n=300)
    r1 = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    r2 = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    assert r1 == r2, "TMLE non-deterministic across identical inputs"


def test_tmle_different_seeds_give_different_results() -> None:
    """Sanity check the determinism test isn't accidentally passing
    because the seed isn't being used. Different seeds → different
    point estimates (KFold shuffling differs)."""
    Y, T, X = _synthetic_linear_dgp(n=300)
    r1 = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    r2 = run_tmle_ate(Y=Y, T=T, X=X, seed=1)
    assert r1["point"] != r2["point"], (
        "Different seeds produced identical estimates — seed not threaded "
        "through KFold"
    )


def test_tmle_returns_finite_outputs() -> None:
    """No NaN / Inf leakage in any of the returned fields. The
    propensity clip + the epsilon-fallback together guarantee this
    on any non-degenerate DGP."""
    Y, T, X = _synthetic_linear_dgp(n=200)
    result = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    for key in ("point", "ci_lower", "ci_upper", "epsilon"):
        v = result[key]
        assert isinstance(v, float) and np.isfinite(v), f"{key} not finite: {v}"


def test_tmle_propensity_diagnostics_populated() -> None:
    """The propensity-diagnostics fields used by the operator card
    are populated with sensible values."""
    Y, T, X = _synthetic_linear_dgp(n=300)
    result = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    q = result["g_quantiles"]
    assert set(q.keys()) == {"p05", "p25", "p50", "p75", "p95"}
    # Quantiles must be monotone non-decreasing.
    assert q["p05"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p95"]
    # All quantiles must be in (0, 1) — propensities are probabilities
    # and the clip enforces a 2.5% floor / ceiling.
    for v in q.values():
        assert 0.0 < v < 1.0, f"propensity quantile out of range: {v}"
    # n_extreme is the count of clipped rows; bounded by n.
    assert 0 <= result["g_n_extreme"] <= result["n_samples"]


def test_tmle_epsilon_is_small_when_nuisance_is_well_specified() -> None:
    """The targeting coefficient ε measures how much the targeting
    step corrected the plug-in estimate. On a well-specified linear
    DGP, the nuisance fit is already close to the truth, so |ε|
    should be small. This is NOT a hard correctness check — it's
    a 'smoke test' that the targeting step isn't doing weird work.
    """
    Y, T, X = _synthetic_linear_dgp(n=500)
    result = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    # On the linear DGP with n=500 the OLS nuisance is well-specified
    # and |epsilon| stays under 0.5. Larger values would indicate
    # targeting did meaningful bias correction → flag for a future
    # auto-challenge.
    assert abs(result["epsilon"]) < 0.5, (
        f"epsilon = {result['epsilon']:.3f} unexpectedly large on a "
        f"well-specified DGP — targeting did more bias correction than "
        f"the nuisance models warranted"
    )


def test_tmle_handles_small_treatment_arm() -> None:
    """When one treatment arm is small (e.g. 10% treated), n_folds
    is internally bounded by the smaller class count so KFold doesn't
    crash."""
    rng = np.random.default_rng(0)
    n = 200
    X = rng.standard_normal((n, 2))
    # ~10% treated.
    T = (rng.uniform(size=n) < 0.10).astype(int)
    Y = 1.5 * T + 0.5 * X[:, 0] + 0.3 * rng.standard_normal(n)
    if T.sum() < 10:
        pytest.skip("RNG produced too few treated rows for this test")
    result = run_tmle_ate(Y=Y, T=T, X=X, n_folds=5, seed=0)
    # Should return a finite estimate, not raise.
    assert np.isfinite(result["point"])


# ── Input validation ────────────────────────────────────────────────


def test_tmle_rejects_no_variation_in_treatment() -> None:
    """All-treated or all-control inputs are degenerate — TMLE can't
    estimate a contrast and should raise clearly rather than producing
    a misleading number."""
    n = 100
    X = np.random.default_rng(0).standard_normal((n, 2))
    T = np.zeros(n, dtype=int)  # all control
    Y = 0.5 * X[:, 0] + np.random.default_rng(0).standard_normal(n)
    with pytest.raises(ValueError, match="requires both treated and control"):
        run_tmle_ate(Y=Y, T=T, X=X, seed=0)


def test_tmle_rejects_wrong_input_shapes() -> None:
    """Wrong-shape inputs should fail at the boundary with a clear
    message rather than crashing somewhere deep in sklearn."""
    with pytest.raises(ValueError, match="Expected Y"):
        run_tmle_ate(
            Y=np.zeros((10, 1)),  # 2-D instead of 1-D
            T=np.zeros(10, dtype=int),
            X=np.zeros((10, 2)),
            seed=0,
        )


def test_tmle_rejects_mismatched_row_counts() -> None:
    with pytest.raises(ValueError, match="Row counts must match"):
        run_tmle_ate(
            Y=np.zeros(10),
            T=np.zeros(8, dtype=int),
            X=np.zeros((10, 2)),
            seed=0,
        )


# ── Sample size scaling ─────────────────────────────────────────────


@pytest.mark.parametrize("n", [100, 300, 1000])
def test_tmle_error_decreases_with_n(n: int) -> None:
    """At larger n, the asymptotic theory applies more cleanly and
    the TMLE error should shrink. Not a strict monotone check (a
    single seed is noisy) — just that error stays bounded at the
    Layer 19 contract even at modest n."""
    Y, T, X = _synthetic_linear_dgp(n=n, true_ate=2.0, seed=42)
    result = run_tmle_ate(Y=Y, T=T, X=X, seed=0)
    error = abs(result["point"] - 2.0)
    assert error <= 0.20, (
        f"At n={n}, |error| = {error:.3f} > 0.20 bound"
    )


# ── Tier B: engine dispatch integration ─────────────────────────────


def _dowhy_available() -> bool:
    try:
        import dowhy  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _dowhy_available(),
    reason="Tier B integration tests require dowhy (eval-gate lane installs it)",
)


def test_engine_dispatch_routes_tmle_through_tmle_module() -> None:
    """The fan-out's ``_run_one_estimator`` must route method_key='tmle'
    to ``_run_one_tmle`` and back a valid ``CounterfactualEstimate``."""
    from counterfactual_service.engine import _run_one_estimator
    from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

    n = 200
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "x1": rng.standard_normal(n),
        "x2": rng.standard_normal(n),
        "t":  (rng.uniform(size=n) < 0.5).astype(int),
        "y":  rng.standard_normal(n),
    })
    df["y"] = 2.0 * df["t"] + 1.0 * df["x1"] + 0.3 * rng.standard_normal(n)
    dag = {
        "nodes": ["x1", "x2", "t", "y"],
        "edges": [("x1", "t"), ("x2", "t"), ("x1", "y"), ("t", "y")],
    }
    est = _run_one_estimator(
        method_key="tmle",
        df=df,
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(
            column="y", agg="mean",
            window=("2026-01-01", "2026-12-31"),
        ),
        dag=dag,
        seed=0,
    )
    assert est.method == "tmle"
    assert est.error is None
    assert est.n_samples == n
    assert est.propensity_diagnostics is not None
    # The CI must bracket the point.
    assert est.ci_lower <= est.point <= est.ci_upper


def test_engine_dispatch_tmle_byte_identical_across_runs() -> None:
    """Engine-side dispatch preserves the TMLE byte-identity contract.
    Required for Layer 10 — if a future engine change introduces non-
    determinism via the dispatch wrapper, this test catches it."""
    from counterfactual_service.engine import _run_one_estimator
    from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

    n = 150
    rng = np.random.default_rng(7)
    df = pd.DataFrame({
        "x1": rng.standard_normal(n),
        "t":  (rng.uniform(size=n) < 0.5).astype(int),
    })
    df["y"] = 1.5 * df["t"] + 0.4 * df["x1"] + 0.3 * rng.standard_normal(n)
    dag = {
        "nodes": ["x1", "t", "y"],
        "edges": [("x1", "t"), ("x1", "y"), ("t", "y")],
    }
    spec = dict(
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(
            column="y", agg="mean",
            window=("2026-01-01", "2026-12-31"),
        ),
        dag=dag,
        seed=42,
    )
    est1 = _run_one_estimator(method_key="tmle", df=df.copy(), **spec)
    est2 = _run_one_estimator(method_key="tmle", df=df.copy(), **spec)
    assert est1.point == est2.point
    assert est1.ci_lower == est2.ci_lower
    assert est1.ci_upper == est2.ci_upper
