"""BUG-215: 2SLS must not return an authoritative CI from a weak or degenerate instrument."""
import numpy as np
import pandas as pd
import pytest

from counterfactual_service.iv_estimator import (
    WEAK_INSTRUMENT_F,
    WeakInstrumentError,
    first_stage_diagnostics,
    run_iv_2sls,
)


def _data(strength, n=3000, seed=0):
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n)
    u = rng.normal(size=n)
    t = strength * z + 0.7 * u + rng.normal(0, 0.3, n)
    y = 2.0 * t + 1.5 * u + rng.normal(0, 0.3, n)
    return pd.DataFrame({"z": z, "t": t, "y": y})


def test_strong_instrument_has_a_large_first_stage_f():
    d = first_stage_diagnostics(_data(1.0), "t", ["z"], [])
    assert d["first_stage_f"] > 100 and d["design_rank"] == d["design_columns"]


def test_weak_instrument_has_a_small_first_stage_f():
    assert first_stage_diagnostics(_data(0.01, n=400), "t", ["z"], [])["first_stage_f"] < WEAK_INSTRUMENT_F


def test_weak_instrument_is_refused_when_enforced():
    with pytest.raises(WeakInstrumentError, match="weak instrument"):
        run_iv_2sls(_data(0.01, n=400), "t", "y", ["z"], [], min_first_stage_f=WEAK_INSTRUMENT_F)


def test_strong_instrument_still_estimates_when_enforced():
    point, lo, hi = run_iv_2sls(_data(1.0), "t", "y", ["z"], [], min_first_stage_f=WEAK_INSTRUMENT_F)
    assert 1.6 < point < 2.4 and lo < point < hi


def test_default_call_is_unchanged_for_direct_callers():
    # no enforcement unless asked: a weak instrument still returns numbers (old behaviour)
    point, lo, hi = run_iv_2sls(_data(0.01, n=400), "t", "y", ["z"], [])
    assert np.isfinite([point, lo, hi]).all()


def test_rank_deficient_design_is_refused_when_enforced():
    d = _data(1.0)
    d["x"] = d["z"] * 2.0  # confounder perfectly collinear with the instrument
    with pytest.raises(WeakInstrumentError, match="rank-deficient"):
        run_iv_2sls(d, "t", "y", ["z"], ["x"], min_first_stage_f=WEAK_INSTRUMENT_F)


def _engine_estimate(df):
    from counterfactual_service.engine import _run_one_estimator
    from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

    return _run_one_estimator(
        "iv", df,
        InterventionSpec(column="t", actual=1, counterfactual=0),
        OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        {"edges": [["z", "t"], ["t", "y"]]}, seed=0,
    )


def test_engine_reports_a_weak_instrument_as_an_errored_estimate():
    est = _engine_estimate(_data(0.01, n=400))
    assert est.method == "iv"
    assert est.error and "weak instrument" in est.error


def test_engine_still_estimates_with_a_strong_instrument():
    est = _engine_estimate(_data(1.0))
    assert est.error is None and est.ci_lower < est.point < est.ci_upper
