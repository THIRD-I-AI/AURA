"""BUG-293: the estimator families answered different questions unless
treatment.actual == 1 on a 0/1 column. The DR-class estimators compared "rows equal to
actual" with all other rows; the DoWhy estimators and IV returned the effect of a +1
change in the column; treatment.counterfactual was never read. With actual=0,
counterfactual=1 they had opposite signs and the headline averaged them."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from counterfactual_service import engine
from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

TRUE_EFFECT = 0.30  # outcome under t=1 minus outcome under t=0
DAG = {"edges": [("x", "t"), ("x", "y"), ("t", "y")]}
OUTCOME = OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01"))


def _df(n: int = 3000) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    x = rng.normal(0, 1, n)
    t = ((0.5 * x + rng.normal(0, 1, n)) > 0).astype(float)
    y = 0.4 * x + TRUE_EFFECT * t + rng.normal(0, 0.2, n)
    return pd.DataFrame({"x": x, "t": t, "y": y})


def _estimate(method: str, actual: float, counterfactual: float, df: pd.DataFrame | None = None):
    est = engine._run_one_estimator(
        method, df if df is not None else _df(),
        InterventionSpec(column="t", actual=actual, counterfactual=counterfactual),
        OUTCOME, DAG, seed=0,
    )
    assert est.error is None, est.error
    return est


@pytest.mark.parametrize("method", ["linear_regression", "tmle"])
def test_actual_one_vs_zero_is_the_plain_effect(method):
    pytest.importorskip("dowhy")
    est = _estimate(method, 1.0, 0.0)
    assert est.point == pytest.approx(TRUE_EFFECT, abs=0.05)
    assert est.ci_lower <= est.point <= est.ci_upper


@pytest.mark.parametrize("method", ["linear_regression", "tmle"])
def test_actual_zero_vs_one_is_the_reverse_effect_for_every_family(method):
    """The contrast 'outcome at 0 minus outcome at 1' is -0.30 whichever estimator runs."""
    pytest.importorskip("dowhy")
    est = _estimate(method, 0.0, 1.0)
    assert est.point == pytest.approx(-TRUE_EFFECT, abs=0.05)
    assert est.ci_lower <= est.point <= est.ci_upper


def test_double_ml_agrees_with_the_dowhy_estimators_when_actual_is_zero():
    pytest.importorskip("dowhy")
    pytest.importorskip("econml")
    dml = _estimate("double_ml", 0.0, 1.0)
    lin = _estimate("linear_regression", 0.0, 1.0)
    assert dml.point == pytest.approx(lin.point, abs=0.08)
    assert dml.point < 0 and lin.point < 0


def test_dr_class_compares_actual_with_counterfactual_not_with_every_other_row():
    """A third treatment level with a very different outcome must not be pooled into
    the comparison group."""
    pytest.importorskip("sklearn")  # tmle; the base CI lane has no causal dependencies
    df = _df()
    rng = np.random.default_rng(5)
    other = pd.DataFrame({"x": rng.normal(0, 1, 1500), "t": 2.0, "y": rng.normal(50, 1, 1500)})
    mixed = pd.concat([df, other], ignore_index=True)

    est = _estimate("tmle", 1.0, 0.0, df=mixed)

    assert est.point == pytest.approx(TRUE_EFFECT, abs=0.06)


def test_a_per_unit_slope_is_scaled_to_the_requested_contrast():
    assert engine._per_unit_to_contrast(0.5, 0.4, 0.6, InterventionSpec(column="t", actual=3, counterfactual=1)) \
        == pytest.approx((1.0, 0.8, 1.2))
    point, lo, hi = engine._per_unit_to_contrast(0.5, 0.4, 0.6, InterventionSpec(column="t", actual=0, counterfactual=1))
    assert (point, lo, hi) == pytest.approx((-0.5, -0.6, -0.4))
