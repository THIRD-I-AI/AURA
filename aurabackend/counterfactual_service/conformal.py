"""
Conformal inference primitives for the Counterfactual Audit Engine.

Sprint 16 — anchors:
  * Lei, J. & Candès, E. J. (2021). Conformal Inference of Counterfactuals
    and Individual Treatment Effects. JRSS-B 83(5):911-938.
  * Alaa, A., Ahmad, Z. & van der Laan, M. (NeurIPS 2023). Conformal
    Meta-learners for Predictive Inference of Individual Treatment Effects.
  * Tibshirani, R. J., Barber, R. F., Candès, E. J. & Ramdas, A. (2019).
    Conformal Prediction Under Covariate Shift. NeurIPS 32 (weighted split
    conformal — Theorem 2, Algorithm 1).

What lives in this module:

Distribution-free finite-sample quantile inference for the ATE,
producing a confidence interval whose coverage holds at the stated
``1-alpha`` level **regardless of whether the nuisance models are
correctly specified** — the only requirement is that the calibration
set is iid from the same distribution as the deployment population.

The companion engine path
(``engine._run_one_econml_dr_learner`` / ``..._forest_dr_learner``
called with ``conformal_calibration=True``) does the actual split:

  1. Partition the data 70/30 into proper-train / calibration using a
     seed derived from the engine's per-method seed.
  2. Fit the DR-Learner on proper-train.
  3. Compute AIPW pseudo-outcomes on the calibration rows — these are
     unbiased estimates of the row-level CATE under the DR contract.
  4. Use the calibration-set mean as the conformal point estimate and
     the (1-alpha) empirical quantile of the absolute deviations as
     the half-width.

The conformal contract trades a slightly wider interval for a coverage
guarantee that doesn't depend on the nuisance models being correct —
the operator-facing payoff of Sprint 16 over Sprint 12's asymptotic
statsmodels sandwich.
"""
from __future__ import annotations

from typing import Optional

import numpy as np


def weighted_split_conformal(
    scores: np.ndarray,
    weights: Optional[np.ndarray] = None,
    alpha: float = 0.05,
) -> float:
    """Return the (1-alpha) weighted empirical quantile of ``scores``.

    This is the core split-conformal primitive: given a vector of
    calibration-set conformity scores (typically ``|psi_i - mean(psi)|``
    for an ATE estimator's per-row AIPW pseudo-outcome), return the
    half-width such that ``P(|psi_new - mean(psi)| <= q) >= 1 - alpha``
    in finite samples.

    Uniform weights (``weights=None``) recover the standard split-
    conformal quantile (Vovk et al. 2005). Non-uniform weights are the
    Tibshirani-Barber-Candès-Ramdas (2019) covariate-shift correction;
    pass ``1 / e_hat(X)`` for treated rows and ``1 / (1 - e_hat(X))``
    for control rows when you need that.

    The (1-alpha) quantile of the *empirical CDF* of the scores has a
    finite-sample conservativeness correction baked in:

      q = quantile( scores, ceil((n+1) * (1-alpha)) / n )

    so coverage holds at ``>= 1 - alpha`` (the Vovk-Petej finite-sample
    guarantee, not just asymptotically). For weighted scores we use
    the weighted analogue:

      F_w(q) = sum_{i : s_i <= q} w_i / sum_i w_i
      q = inf { x : F_w(x) >= (1-alpha) * (sum_i w_i + 1) / sum_i w_i }

    The ``+1`` term in the denominator-numerator accounts for the
    deployment row (assumed iid with the calibration rows).

    Args:
        scores: 1-D array of non-negative conformity scores. The
            algorithm doesn't require non-negativity but the ATE
            integration always passes absolute deviations, so the
            output is interpretable as a half-width.
        weights: optional 1-D array, same length as ``scores``. None
            means uniform (standard split-conformal).
        alpha: miscoverage tolerance in ``(0, 1)``. The conformal
            interval has coverage ``>= 1 - alpha`` in finite samples.

    Returns:
        The conformal half-width as a Python float.

    Raises:
        ValueError if scores is empty, alpha is out of (0, 1), or
        weights has a different length than scores.
    """
    if alpha <= 0.0 or alpha >= 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    s = np.asarray(scores, dtype=float).ravel()
    if s.size == 0:
        raise ValueError("scores must be non-empty for conformal inference")

    if weights is None:
        # Vovk-Petej finite-sample correction: use the
        # ceil((n+1) * (1-alpha))-th order statistic. When that index
        # exceeds n (insufficient calibration data to certify
        # coverage), return +inf — the interval becomes uninformative
        # and the caller can decide to fall back to asymptotic. This
        # is the conservative reading of the conformal contract: an
        # estimator that admits "I cannot certify coverage at this
        # alpha given n samples" is more honest than one that fakes a
        # tight interval.
        n = s.size
        k = int(np.ceil((n + 1) * (1.0 - alpha)))
        if k > n:
            return float("inf")
        # np.partition / quantile both work; partition is O(n) and
        # matches the (n+1)*(1-alpha) order statistic exactly.
        return float(np.partition(s, k - 1)[k - 1])

    w = np.asarray(weights, dtype=float).ravel()
    if w.size != s.size:
        raise ValueError(
            f"weights length {w.size} must match scores length {s.size}"
        )
    if (w < 0).any():
        raise ValueError("weights must be non-negative")
    total_w = float(w.sum())
    if total_w <= 0.0:
        raise ValueError("at least one weight must be positive")

    # Weighted split-conformal (Tibshirani-Barber-Candes-Ramdas 2019,
    # Algorithm 1). Sort by score, accumulate normalised weight,
    # return the smallest score whose CDF >= (1-alpha) * (W+1)/W —
    # the +1 accounts for the deployment row's "self-weight" of 1.
    sort_idx = np.argsort(s)
    s_sorted = s[sort_idx]
    w_sorted = w[sort_idx]
    # Normalise weights to include a self-weight of 1 for the new row
    norm = w_sorted / (total_w + 1.0)
    cum = np.cumsum(norm)
    threshold = 1.0 - alpha
    # First index whose cumulative weight (including own) >= threshold
    above = np.where(cum >= threshold)[0]
    if above.size == 0:
        return float("inf")
    return float(s_sorted[above[0]])


def aipw_pseudo_outcomes(
    Y: np.ndarray,
    T: np.ndarray,
    e_hat: np.ndarray,
    mu0_hat: np.ndarray,
    mu1_hat: np.ndarray,
) -> np.ndarray:
    """AIPW (Augmented Inverse Probability Weighted) pseudo-outcomes.

    psi_i = mu1(X_i) - mu0(X_i)
          + (T_i / e(X_i)) * (Y_i - mu1(X_i))
          - ((1 - T_i) / (1 - e(X_i))) * (Y_i - mu0(X_i))

    Each psi_i is an unbiased influence-function-style estimate of
    the row-level CATE. Their mean is the AIPW estimator of the ATE
    and is **doubly robust** — consistent if either ``e_hat`` or
    ``(mu0_hat, mu1_hat)`` is correctly specified. The conformal
    quantile on |psi_i - mean(psi)| gives a CI whose coverage holds
    regardless of which (if either) of the nuisance models is right.

    Clips propensities to ``[0.01, 0.99]`` to keep the IPW
    denominator finite — the matching defensive band Sprint 14's
    propensity warning uses to flag IPW-fragile rows. Calls with
    rows in the fragile band get a warning logged at the engine
    layer (not here — this is the pure-math primitive).

    Args:
        Y: 1-D outcome vector, length n.
        T: 1-D binary treatment vector (0/1), length n.
        e_hat: 1-D propensity estimates, length n. Clipped internally.
        mu0_hat: 1-D outcome predictions under T=0, length n.
        mu1_hat: 1-D outcome predictions under T=1, length n.

    Returns:
        1-D array of pseudo-outcomes, same length as inputs.
    """
    Y = np.asarray(Y, dtype=float).ravel()
    T = np.asarray(T, dtype=float).ravel()
    e_hat = np.clip(np.asarray(e_hat, dtype=float).ravel(), 0.01, 0.99)
    mu0 = np.asarray(mu0_hat, dtype=float).ravel()
    mu1 = np.asarray(mu1_hat, dtype=float).ravel()
    return (
        (mu1 - mu0)
        + (T / e_hat) * (Y - mu1)
        - ((1.0 - T) / (1.0 - e_hat)) * (Y - mu0)
    )
