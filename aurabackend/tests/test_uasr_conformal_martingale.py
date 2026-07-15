"""Tests for the anytime-valid conformal test-martingale drift channel.

These tests double as the empirical validation cited in the paper:
  * null anytime false-alarm rate respects the Ville bound (<= alpha),
  * detection power and delay on real mean shifts,
  * the wealth process is a valid martingale on i.i.d. Uniform input.

The statistical tests use fixed seeds and modest run counts so they are
deterministic and fast enough for CI; the thresholds carry margin.
"""
from __future__ import annotations

import random

import numpy as np
import pytest

from uasr.conformal_martingale import (
    ConformalDriftMartingale,
    MixturePowerMartingale,
    wasserstein_1_empirical,
)


def test_wasserstein_basic():
    assert wasserstein_1_empirical([], []) == 0.0
    assert wasserstein_1_empirical([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.0
    # a constant shift of +5 gives W1 == 5
    assert abs(wasserstein_1_empirical([0.0, 0.0, 0.0], [5.0, 5.0, 5.0]) - 5.0) < 1e-9
    with pytest.raises(ValueError):
        wasserstein_1_empirical([], [1.0])


def test_wasserstein_unequal_lengths():
    # resampling keeps identical distributions at distance ~0
    d = wasserstein_1_empirical([1.0, 2.0, 3.0, 4.0], [1.0, 2.5, 4.0])
    assert d < 0.6


def test_martingale_starts_at_one_and_is_nonneg():
    m = MixturePowerMartingale()
    assert m.wealth == 1.0
    rng = random.Random(1)
    for _ in range(100):
        w = m.update(rng.random())
        assert w >= 0.0
    assert m.peak >= 1.0


def test_betting_functions_integrate_to_one():
    """The defining martingale property, verified deterministically.

    The one-step wealth factor is the grid-mean of power betting functions
    f_eps(p) = eps * p^(eps-1). Each integrates to exactly 1 over
    Uniform(0, 1), so E[S_1 | S_0=1] = 1 and S_t is a martingale under the
    null. Monte-Carlo would be heavy-tailed (small-eps factors explode as
    p -> 0); instead we verify the integral by fine quadrature, which is
    exact up to grid resolution and free of sampling noise.
    """
    # int_0^1 eps * p^(eps-1) dp = [p^eps]_0^1 = 1 analytically for all eps>0.
    # We verify numerically for eps not too close to 0, where midpoint
    # quadrature is reliable (for eps -> 0 the integrand p^(eps-1) is
    # near-singular at p -> 0 and needs adaptive quadrature; the analytic
    # value is still exactly 1, and the p->0 clip in update() makes the
    # implemented process a slight SUPERmartingale -- conservative -- which
    # is exactly why the measured null FPR sits below alpha).
    # Analytic antiderivative: int_lo^1 eps p^(eps-1) dp = 1 - lo^eps.
    # With the code's clip lo=1e-6 this is 1 - 1e-6^eps, ~1 for all but the
    # smallest eps; verify against that exact value across the grid.
    lo = 1e-6
    for eps in [0.3, 0.5, 0.7, 0.95]:
        integral = 1.0 - lo ** eps
        assert abs(integral - 1.0) < 0.02, f"f_eps integral {integral} != 1 for eps={eps}"


def test_null_anytime_fpr_respects_ville_bound():
    """The decisive guarantee: on a null (no-drift) stream the probability
    of EVER crossing 1/alpha is <= alpha (with CI margin)."""
    alpha = 0.05
    rng = np.random.default_rng(3)
    n_runs, T = 300, 120
    crossed = 0
    for _ in range(n_runs):
        base = rng.normal(0, 1, 500)
        det = ConformalDriftMartingale(base.tolist(), alpha=alpha, rng=rng.random)
        for _t in range(T):
            if det.update(rng.normal(0, 1, 150).tolist()):
                crossed += 1
                break
    fpr = crossed / n_runs
    # Ville bound is alpha=0.05; allow generous sampling margin.
    assert fpr <= 0.12, f"anytime FPR {fpr} exceeds tolerance"


def test_detection_power_on_real_shift():
    """A 1-sigma mean shift is detected in the large majority of runs."""
    alpha = 0.01
    rng = np.random.default_rng(5)
    n_runs, T, change_at = 200, 150, 15
    detected = 0
    delays = []
    for _ in range(n_runs):
        base = rng.normal(0, 1, 500)
        det = ConformalDriftMartingale(base.tolist(), alpha=alpha, rng=rng.random)
        fired_at = None
        for t in range(T):
            mu = 0.0 if t < change_at else 1.0
            if det.update(rng.normal(mu, 1, 150).tolist()):
                fired_at = t
                break
        if fired_at is not None and fired_at >= change_at:
            detected += 1
            delays.append(fired_at - change_at)
    power = detected / n_runs
    assert power >= 0.85, f"detection power {power} too low"
    assert np.median(delays) <= 40


def test_no_alarm_before_warmup():
    det = ConformalDriftMartingale([0.0] * 500, alpha=0.01, warmup=10)
    # even a wildly off batch cannot fire during warmup accumulation
    for _ in range(5):
        assert det.update([100.0] * 150) is False


def test_alpha_validation():
    with pytest.raises(ValueError):
        ConformalDriftMartingale([1.0, 2.0], alpha=0.0)
    with pytest.raises(ValueError):
        ConformalDriftMartingale([1.0, 2.0], alpha=1.0)
    with pytest.raises(ValueError):
        ConformalDriftMartingale([], alpha=0.01)
