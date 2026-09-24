"""BUG-155: DoWhy CI extraction must never fabricate a zero-width interval.

backend.linear_regression returns its interval as a (1, 2) array -- ``[[lo, hi]]``
-- which the engine's old ``len(ci) >= 2`` guard rejected. It then fell back to
``getattr(est, "stderr", 0.0)``, an attribute DoWhy estimates do not have, and
reported ``[point, point]``. significance_verdict() reads any interval that
excludes zero as certainty, so the fake interval could flip a fairness/
compliance verdict to "detected".

Tier A (pure helper tests, no DoWhy) + Tier B (real DoWhy, skipped without it).
"""
from __future__ import annotations

import numpy as np
import pytest

from counterfactual_service.engine import _dowhy_confidence_interval, dowhy_available


class _Est:
    """Minimal stand-in for a DoWhy CausalEstimate."""

    def __init__(self, ci=None, se=None, ci_raises=False, se_raises=False):
        self._ci, self._se = ci, se
        self._ci_raises, self._se_raises = ci_raises, se_raises

    def get_confidence_intervals(self):
        if self._ci_raises:
            raise RuntimeError("no bootstrap available")
        return self._ci

    def get_standard_error(self):
        if self._se_raises:
            raise RuntimeError("no stderr")
        return self._se


def test_linear_regression_nested_1x2_array_is_read_not_dropped():
    """The exact shape DoWhy's backdoor.linear_regression returns."""
    est = _Est(ci=np.array([[1.15592418, 1.55275222]]), se=np.array([0.101]))
    assert _dowhy_confidence_interval(est, 1.354) == pytest.approx((1.15592418, 1.55275222))


def test_flat_pair_from_psm_ipw_is_read():
    est = _Est(ci=(np.float64(0.98098), np.float64(1.45922)), se=0.113)
    assert _dowhy_confidence_interval(est, 1.25) == pytest.approx((0.98098, 1.45922))


def test_falls_back_to_the_real_standard_error_when_ci_raises():
    est = _Est(ci_raises=True, se=np.array([0.1]))
    lo, hi = _dowhy_confidence_interval(est, 1.0)
    assert (lo, hi) == pytest.approx((0.8, 1.2))


def test_falls_back_to_standard_error_when_ci_shape_is_unusable():
    est = _Est(ci=[1.0], se=0.25)
    assert _dowhy_confidence_interval(est, 2.0) == pytest.approx((1.5, 2.5))


@pytest.mark.parametrize("se", [0.0, -1.0, float("nan"), float("inf"), None])
def test_no_real_interval_returns_none_instead_of_a_zero_width_one(se):
    """The old fallback turned a missing/zero stderr into [point, point]."""
    est = _Est(ci_raises=True, se=se)
    assert _dowhy_confidence_interval(est, 1.354) is None


def test_estimate_with_no_ci_or_se_api_returns_none():
    assert _dowhy_confidence_interval(object(), 1.0) is None


@pytest.mark.skipif(not dowhy_available(), reason="dowhy not installed")
def test_real_dowhy_linear_regression_reports_a_nonzero_width_ci():
    """Live symptom: linear_regression came back as [point, point]."""
    import pandas as pd

    from counterfactual_service import engine as E
    from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

    rng = np.random.default_rng(0)
    n = 600
    z = rng.normal(size=n)
    t = (rng.normal(size=n) + 0.8 * z > 0).astype(int)
    y = 1.5 * t + 0.7 * z + rng.normal(size=n)
    df = pd.DataFrame({"z": z, "t": t, "y": y})
    est = E._run_one_estimator(
        "linear_regression",
        df,
        InterventionSpec(column="t", actual=1, counterfactual=0),
        OutcomeSpec(column="y", agg="sum", window=("2025-01-01", "2025-12-31")),
        {"edges": [("z", "t"), ("z", "y"), ("t", "y")]},
        7,
    )
    assert est.error is None
    assert est.ci_upper - est.ci_lower > 0.05, (est.ci_lower, est.ci_upper)
    assert est.ci_lower < est.point < est.ci_upper
