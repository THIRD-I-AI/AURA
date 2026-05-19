"""
Cross-fitted Targeted Maximum Likelihood Estimation (TMLE) for the
counterfactual audit engine — Sprint S22.

Anchors
-------
* van der Laan & Rose (2011), *Targeted Learning: Causal Inference for
  Observational and Experimental Data*, Springer. — The canonical
  treatment of TMLE.
* Zheng & van der Laan (2011), *Cross-Validated Targeted Minimum-Loss-
  Based Estimation*. UC Berkeley Working Paper 273. — The cross-fitted
  variant this module implements. Vanilla TMLE fits and updates the
  nuisance on the same data, which over-fits in the small-n regime
  the eval-gate exercises; cross-fitting fixes that.
* van der Laan & Rubin (2006), *Targeted Maximum Likelihood Learning*.
  Int J Biostat 2(1). — Original TMLE for continuous outcomes via the
  identity-link linear submodel this module uses.

What this module ships
----------------------
``run_tmle_ate(Y, T, X, *, n_folds, propensity_clip, seed) -> dict``

Pure-NumPy + sklearn implementation. NO external causal-inference
dependency (no dowhy, no econml). Inputs:

* ``Y``   — (n,) continuous outcome.
* ``T``   — (n,) binary treatment in {0, 1}.
* ``X``   — (n, p) confounder matrix (must be 2-D even for p=1).

Returns a dict with ``point``, ``ci_lower``, ``ci_upper``, ``n_samples``,
``epsilon`` (the targeting coefficient — small absolute value means
the nuisance models were well-specified and targeting did little
work; large value means TMLE made a meaningful correction), plus
propensity diagnostics for the operator card.

Why TMLE in addition to DR-Learner?
-----------------------------------
The S12 ``double_ml`` slot (EconML LinearDRLearner) and the S15
``forest_dr`` slot (EconML ForestDRLearner) are both doubly-robust:
correctly-specified outcome model OR correctly-specified propensity
suffices. But neither achieves the SEMI-PARAMETRIC EFFICIENCY BOUND
in finite samples — the asymptotic variance can be worse than
necessary under mild misspecification.

TMLE's **targeting step** closes that gap. After cross-fitting the
nuisance, it solves a single 1-D optimization that minimises the
plug-in estimate's bias along the efficient influence curve. The
resulting estimator is asymptotically efficient under the SEMI-
parametric efficiency bound (van der Laan & Rose § 5).

For the eval-gate's synthetic DGP (linear-in-X, mild noise) the
practical effect is: TMLE typically recovers TRUE_EFFECT within
MAE 0.20 where DR-Learner ships an 0.30 bound. The auto-challenge
in S22 fires when TMLE and ForestDR disagree by more than 2× the
conformal half-width — that's a signal the underlying assumptions
(positivity, no unmeasured confounders) might be violated.

Algorithm
---------
1. K-fold cross-fit:
   * For each fold k, train Q(A=1,W), Q(A=0,W) outcome regression
     and g(W) = P(A=1|W) propensity on out-of-fold data.
   * Score the fold's rows to get Q1_hat, Q0_hat, g_hat per row.
2. Clip propensities to [propensity_clip, 1 - propensity_clip] —
   prevents the IPW correction blowing up at boundaries.
3. Clever covariate at observed A: H_obs = (2T-1) / [g_hat if T=1
   else 1-g_hat].
4. Targeting (identity-link linear submodel, van der Laan & Rubin
   2006): ε = OLS coefficient of (Y - Q_obs) on H_obs through
   the origin. Closed form: ε = Σ(H·residual) / Σ(H²).
5. Update Q*: Q1* = Q1_hat + ε/g_hat, Q0* = Q0_hat - ε/(1-g_hat).
6. ATE = mean(Q1* - Q0*) — plug-in on the targeted Q*.
7. Influence curve IC = H_obs·(Y - Q_obs*) + (Q1* - Q0*) - ATE.
   Var = mean(IC²)/n. Asymptotic CI = ATE ± 1.96·√Var.

Determinism
-----------
The function takes a single ``seed`` and threads it into every
stochastic component: ``KFold(shuffle=True, random_state=seed)``,
``LogisticRegression(random_state=seed)``, plus
``_seed_numpy(seed)`` at the top. ``LinearRegression`` is analytic
(no random state). Same inputs + same seed → byte-identical
outputs. Required for Layer 10 of the audit-engine contract.

Calibration
-----------
Propensity model is ``LogisticRegression(penalty='l2', max_iter=1000)``
per [[feedback_propensity_calibration]] — RandomForestClassifier's
predict_proba lands near 0/1 boundaries and the IPW correction
``(T-e)/[e(1-e)]`` blows up. L2-regularised logistic gives
well-calibrated probabilities out of the box. CalibratedClassifierCV
is overkill for the linear DGPs the eval-gate hits; a future S22.1
can swap in a calibrated boosted classifier when the DGP becomes
non-linear.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.model_selection import KFold


def _seed_numpy(seed: int) -> None:
    """Pin numpy's global RNG before any stochastic work. Matches the
    engine's ``_seed_numpy`` helper so TMLE participates in the same
    Layer 10 byte-identity contract as the DR estimators."""
    np.random.seed(seed)


def run_tmle_ate(
    Y: np.ndarray,
    T: np.ndarray,
    X: np.ndarray,
    *,
    n_folds: int = 5,
    propensity_clip: float = 0.025,
    seed: int = 0,
) -> Dict[str, Any]:
    """Cross-fitted TMLE for the ATE on continuous Y, binary T.

    Args:
        Y: (n,) continuous outcome.
        T: (n,) binary treatment in {0, 1}.
        X: (n, p) confounder matrix; reshape (n,) into (n, 1) before
            calling if you have a single confounder.
        n_folds: K for K-fold cross-fitting. Bounded internally to
            min(n_folds, smaller-class-count) so a small treatment
            arm doesn't crash with ``ValueError: n_splits cannot be
            greater than the number of members in each class``.
        propensity_clip: clip propensities to
            ``[propensity_clip, 1 - propensity_clip]`` to bound the
            IPW correction. 0.025 is a standard default (~ 2.5% tail
            on each side); deployments with extreme imbalance may
            relax to 0.05 or 0.1.
        seed: drives all stochastic components for Layer 10
            byte-identity.

    Returns:
        Dict with keys:
            point, ci_lower, ci_upper: ATE estimate + asymptotic CI.
            n_samples: input row count.
            epsilon: targeting coefficient — small → nuisance was
                well-specified; large → targeting did meaningful work.
            g_quantiles, g_min, g_max, g_mean, g_n_extreme: propensity
                distribution diagnostics for the operator card.
    """
    if Y.ndim != 1 or T.ndim != 1 or X.ndim != 2:
        raise ValueError(
            f"Expected Y (n,), T (n,), X (n, p); got Y.ndim={Y.ndim}, "
            f"T.ndim={T.ndim}, X.ndim={X.ndim}",
        )
    n = len(Y)
    if not (len(T) == n and len(X) == n):
        raise ValueError(
            f"Row counts must match; got n={n}, len(T)={len(T)}, len(X)={len(X)}",
        )
    n_treated = int(T.sum())
    n_control = n - n_treated
    if n_treated == 0 or n_control == 0:
        raise ValueError(
            f"TMLE requires both treated and control observations; "
            f"got n_treated={n_treated}, n_control={n_control}",
        )

    # Cap n_folds at the smallest class count so KFold doesn't trip
    # on tiny treatment arms. Also enforce a minimum of 2 folds —
    # cross-fitting with 1 fold is just plain TMLE.
    n_folds = max(2, min(n_folds, n_treated, n_control))

    _seed_numpy(seed)

    Q1_hat = np.zeros(n, dtype=float)
    Q0_hat = np.zeros(n, dtype=float)
    g_hat = np.zeros(n, dtype=float)

    kf = KFold(n_splits=n_folds, shuffle=True, random_state=seed)
    for train_idx, test_idx in kf.split(X):
        X_train, X_test = X[train_idx], X[test_idx]
        T_train, Y_train = T[train_idx], Y[train_idx]

        # Propensity: L2-regularised logistic regression. Per
        # [[feedback_propensity_calibration]] this is the right
        # baseline — predict_proba stays well-calibrated.
        g_model = LogisticRegression(
            penalty="l2", max_iter=1000, random_state=seed,
        )
        g_model.fit(X_train, T_train)
        g_test = g_model.predict_proba(X_test)[:, 1]
        g_hat[test_idx] = np.clip(g_test, propensity_clip, 1 - propensity_clip)

        # Outcome: linear regression on [X, T] — predict at T=0 and
        # T=1 for each test row to get Q0_hat and Q1_hat. Simple
        # T-as-additional-feature shape; a future S22.1 can swap in
        # interaction features or a non-linear regressor without
        # changing the targeting step.
        Q_train_features = np.column_stack([X_train, T_train.reshape(-1, 1)])
        Q_model = LinearRegression()
        Q_model.fit(Q_train_features, Y_train)
        X_test_t1 = np.column_stack([X_test, np.ones(len(test_idx))])
        X_test_t0 = np.column_stack([X_test, np.zeros(len(test_idx))])
        Q1_hat[test_idx] = Q_model.predict(X_test_t1)
        Q0_hat[test_idx] = Q_model.predict(X_test_t0)

    # Q_hat at the observed treatment value, one prediction per row.
    Q_obs = np.where(T == 1, Q1_hat, Q0_hat)

    # Clever covariate at observed A:
    #   H_obs[i] = +1/g_hat[i]      if T[i] = 1
    #   H_obs[i] = -1/(1-g_hat[i])  if T[i] = 0
    H_obs = np.where(T == 1, 1.0 / g_hat, -1.0 / (1.0 - g_hat))

    # Targeting step (van der Laan & Rubin 2006 linear submodel):
    #   ε = argmin_ε Σ (Y_i - Q_obs[i] - ε·H_obs[i])²
    # Closed form (regression through origin):
    #   ε = Σ H_obs·(Y - Q_obs) / Σ H_obs²
    # When H_obs has zero variance (degenerate case — never happens
    # in practice with the propensity clip) ε falls back to 0.
    h_sq_sum = float(np.sum(H_obs ** 2))
    if h_sq_sum < 1e-12:
        epsilon = 0.0
    else:
        epsilon = float(np.sum(H_obs * (Y - Q_obs)) / h_sq_sum)

    # Update Q* under both treatment values. H1 = 1/g, H0 = -1/(1-g).
    H1 = 1.0 / g_hat
    H0 = -1.0 / (1.0 - g_hat)
    Q1_star = Q1_hat + epsilon * H1
    Q0_star = Q0_hat + epsilon * H0

    # ATE — plug-in on the targeted Q*.
    ate = float(np.mean(Q1_star - Q0_star))

    # Asymptotic CI via the efficient influence curve.
    #   IC[i] = H_obs[i] · (Y_i - Q*_obs[i]) + (Q1*[i] - Q0*[i]) - ATE
    # Var(ATE) = Σ IC² / n²
    Q_star_obs = np.where(T == 1, Q1_star, Q0_star)
    IC = H_obs * (Y - Q_star_obs) + (Q1_star - Q0_star) - ate
    se = float(np.sqrt(np.sum(IC ** 2)) / n)
    ci_lower = float(ate - 1.96 * se)
    ci_upper = float(ate + 1.96 * se)

    # Propensity diagnostics (matches the LinearDR / ForestDR shape so
    # operator-card rendering is uniform across DR-class estimators).
    p_q = np.quantile(g_hat, [0.05, 0.25, 0.5, 0.75, 0.95])
    n_extreme = int(np.sum((g_hat < 0.05) | (g_hat > 0.95)))

    return {
        "point": ate,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "n_samples": n,
        "epsilon": epsilon,
        "g_quantiles": {
            "p05": float(p_q[0]),
            "p25": float(p_q[1]),
            "p50": float(p_q[2]),
            "p75": float(p_q[3]),
            "p95": float(p_q[4]),
        },
        "g_min": float(g_hat.min()),
        "g_max": float(g_hat.max()),
        "g_mean": float(g_hat.mean()),
        "g_n_extreme": n_extreme,
    }


__all__ = ["run_tmle_ate"]
