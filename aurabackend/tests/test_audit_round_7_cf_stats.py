"""Audit round 7, counterfactual statistics: BUG-486, BUG-488, BUG-489.

Each test simulates data with a known true effect and asserts the engine now
recovers it (and that the pre-fix number was measurably wrong)."""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pandas as pd
import pytest

from counterfactual_service import engine
from counterfactual_service.schemas import CounterfactualEstimate, InterventionSpec, OutcomeSpec

OUTCOME = OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01"))


# ── BUG-486: IV must not condition on mediators downstream of the treatment ──

def _iv_df(n: int = 5000) -> pd.DataFrame:
    rng = np.random.default_rng(486)
    z = rng.normal(0, 1, n)
    u = rng.normal(0, 1, n)  # unobserved confounder of t and y
    t = ((1.0 * z + u + rng.normal(0, 1, n)) > 0).astype(float)
    m = 2.0 * t + rng.normal(0, 1, n)
    y = 1.0 * t + 1.0 * m + 2.0 * u + rng.normal(0, 1, n)
    return pd.DataFrame({"z": z, "t": t, "m": m, "y": y})


def test_bug_486_iv_recovers_total_effect_through_a_mediator():
    from counterfactual_service.iv_estimator import run_iv_2sls

    df = _iv_df()
    true_total = 3.0  # direct 1 + 2 * 1 through m
    # The pre-fix control set {m} blocks the mediated path and opens collider bias.
    biased, _, _ = run_iv_2sls(df, "t", "y", ["z"], ["m"])
    assert abs(biased - true_total) > 1.0

    dag = {"edges": [("z", "t"), ("t", "m"), ("m", "y"), ("t", "y")]}
    est = engine._run_one_estimator(
        "iv", df, InterventionSpec(column="t", actual=1.0, counterfactual=0.0),
        OUTCOME, dag, seed=0,
    )
    assert est.error is None, est.error
    assert est.point == pytest.approx(true_total, abs=0.6)
    assert est.ci_lower <= true_total <= est.ci_upper


def test_bug_486_iv_still_adjusts_for_a_non_descendant_parent_of_the_outcome():
    rng = np.random.default_rng(4861)
    n = 5000
    z = rng.normal(0, 1, n)
    w = rng.normal(0, 1, n)  # exogenous cause of y only: kept as a precision control
    u = rng.normal(0, 1, n)
    t = ((z + u + rng.normal(0, 1, n)) > 0).astype(float)
    y = 2.0 * t + 3.0 * w + u + rng.normal(0, 1, n)
    df = pd.DataFrame({"z": z, "w": w, "t": t, "y": y})
    dag = {"edges": [("z", "t"), ("w", "y"), ("t", "y")]}
    est = engine._run_one_estimator(
        "iv", df, InterventionSpec(column="t", actual=1.0, counterfactual=0.0),
        OUTCOME, dag, seed=0,
    )
    assert est.error is None, est.error
    assert est.point == pytest.approx(2.0, abs=0.5)


# ── BUG-488: binary-outcome E-value is computed from the risk ratio ──

def _binary_df(p0: float, p1: float, n_per_arm: int = 1000) -> pd.DataFrame:
    k0, k1 = int(round(p0 * n_per_arm)), int(round(p1 * n_per_arm))
    y0 = np.r_[np.ones(k0), np.zeros(n_per_arm - k0)]
    y1 = np.r_[np.ones(k1), np.zeros(n_per_arm - k1)]
    return pd.DataFrame({
        "t": np.r_[np.zeros(n_per_arm), np.ones(n_per_arm)],
        "y": np.r_[y0, y1],
    })


def _evalue_for(df: pd.DataFrame, point: float, half_width: float,
                actual: float = 1.0, counterfactual: float = 0.0):
    est = CounterfactualEstimate(
        method="linear_regression", point=point,
        ci_lower=point - half_width, ci_upper=point + half_width,
        n_samples=len(df), elapsed_ms=0.0,
    )
    engine._attach_sensitivity(
        [est], df=df,
        treatment=InterventionSpec(column="t", actual=actual, counterfactual=counterfactual),
        outcome=OUTCOME, dag={"edges": [("t", "y")]},
    )
    assert est.sensitivity is not None
    return est.sensitivity


def _vdw(rr: float) -> float:
    rr = max(rr, 1.0 / rr)
    return rr + math.sqrt(rr * (rr - 1.0))


@pytest.mark.parametrize("p0,p1", [(0.80, 0.90), (0.05, 0.15)])
def test_bug_488_binary_outcome_evalue_matches_vanderweele_ding(p0, p1):
    df = _binary_df(p0, p1)
    s = _evalue_for(df, point=p1 - p0, half_width=0.02)
    true_e = _vdw(p1 / p0)  # 1.50 for common, 5.45 for rare
    assert s.e_value_point == pytest.approx(true_e, abs=0.01)
    assert s.rr_approx == pytest.approx(p1 / p0, abs=1e-6)
    # CI limit closer to null: RD = (p1 - p0) - 0.02 over the same baseline risk.
    assert s.e_value_ci == pytest.approx(_vdw((p1 - 0.02) / p0), abs=0.01)
    # The pre-fix Chinn conversion gave 1.90 / 2.05: off by > 0.3 in both directions.
    sd = float(np.std(df["y"].to_numpy(dtype=float), ddof=1))
    chinn = _vdw(math.exp(0.91 * abs((p1 - p0) / sd)))
    assert abs(chinn - true_e) > 0.3


def test_bug_488_binary_outcome_reversed_contrast_uses_counterfactual_arm_as_baseline():
    df = _binary_df(0.80, 0.90)
    # actual=0, counterfactual=1: the contrast is 0.80 - 0.90 over baseline 0.90.
    s = _evalue_for(df, point=-0.10, half_width=0.02, actual=0.0, counterfactual=1.0)
    assert s.e_value_point == pytest.approx(_vdw(0.80 / 0.90), abs=0.01)


def test_bug_488_continuous_outcome_keeps_chinn_conversion():
    rng = np.random.default_rng(488)
    df = pd.DataFrame({"t": rng.integers(0, 2, 2000).astype(float), "y": rng.normal(0, 2, 2000)})
    s = _evalue_for(df, point=0.5, half_width=0.1)
    sd = float(np.std(df["y"].to_numpy(dtype=float), ddof=1))
    assert s.e_value_point == pytest.approx(_vdw(math.exp(0.91 * 0.5 / sd)), abs=1e-4)


# ── BUG-489: refuter estimate_after is on the same contrast scale as estimates ──

def _refuter_df(n: int = 2000) -> pd.DataFrame:
    rng = np.random.default_rng(489)
    x = rng.normal(0, 1, n)
    t = ((0.5 * x + rng.normal(0, 1, n)) > 0).astype(float)
    y = 0.4 * x + 5.0 * t + rng.normal(0, 0.5, n)
    return pd.DataFrame({"x": x, "t": t, "y": y})


def test_bug_489_refuters_report_actual_minus_counterfactual_contrast():
    pytest.importorskip("dowhy")
    df = _refuter_df()
    dag = {"edges": [("x", "t"), ("x", "y"), ("t", "y")]}
    treatment = InterventionSpec(column="t", actual=0.0, counterfactual=1.0)
    est = engine._run_one_estimator("linear_regression", df, treatment, OUTCOME, dag, seed=0)
    assert est.point == pytest.approx(-5.0, abs=0.2)

    refs = asyncio.run(engine.run_refuters(
        df, treatment, OUTCOME, dag,
        refuters=["random_common_cause", "data_subset"], request_hash="bug489",
    ))
    for r in refs:
        assert r.error is None, r.error
        assert r.estimate_after == pytest.approx(est.point, abs=0.3), r.refuter
        assert r.passed is True


def test_bug_489_refuters_scale_by_the_contrast_width():
    pytest.importorskip("dowhy")
    rng = np.random.default_rng(4891)
    n = 2000
    x = rng.normal(0, 1, n)
    t = rng.uniform(0, 15, n) + x
    y = 0.4 * x + 1.0 * t + rng.normal(0, 0.5, n)
    df = pd.DataFrame({"x": x, "t": t, "y": y})
    dag = {"edges": [("x", "t"), ("x", "y"), ("t", "y")]}
    treatment = InterventionSpec(column="t", actual=10.0, counterfactual=5.0)
    refs = asyncio.run(engine.run_refuters(
        df, treatment, OUTCOME, dag, refuters=["data_subset"], request_hash="bug489b",
    ))
    assert refs[0].error is None, refs[0].error
    assert refs[0].estimate_after == pytest.approx(5.0, abs=0.2)
