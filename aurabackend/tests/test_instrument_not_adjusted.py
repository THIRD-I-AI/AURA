"""BUG-405: the backdoor estimators must not adjust for the declared instrument."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from counterfactual_service.audit_mapping import build_dag_from_mapping
from counterfactual_service.engine import _adjustment_set, run_estimators
from counterfactual_service.schemas import InterventionSpec, OutcomeSpec

_COLUMNS = ["flag", "approved", "income", "dti", "officer"]


def _dag(confounders, instrument) -> dict:
    return {"edges": build_dag_from_mapping("flag", "approved", confounders, instrument).edges}


def test_the_instrument_is_left_out_of_the_adjustment_set():
    dag = _dag(["income", "dti"], "officer")
    assert _adjustment_set(dag, "flag", "approved", _COLUMNS) == ["dti", "income"]


def test_confounders_are_all_kept_when_no_instrument_is_declared():
    dag = _dag(["income", "dti"], None)
    assert _adjustment_set(dag, "flag", "approved", _COLUMNS) == ["dti", "income"]


def test_a_control_missing_from_the_data_is_dropped():
    dag = _dag(["income", "dti"], None)
    assert _adjustment_set(dag, "flag", "approved", ["flag", "approved", "income"]) == ["income"]


@pytest.mark.asyncio
async def test_an_estimate_under_unmeasured_confounding_is_not_pushed_further_off():
    # TMLE needs scikit-learn, which only CI's causal lane installs (it lists this file).
    pytest.importorskip("sklearn")
    # u confounds t and y and is not in the data; z is a strong instrument. The true
    # effect is 1. Conditioning on z removes the exogenous variation in t and leaves
    # the share driven by u, so the old adjustment set gave a larger bias.
    rng = np.random.default_rng(7)
    n = 6000
    u = rng.normal(size=n)
    z = rng.integers(0, 2, n).astype(float)
    x = rng.normal(size=n)
    t = (2.5 * z + 1.0 * u + 0.5 * x + rng.normal(size=n) > 1.25).astype(float)
    y = 1.0 * t + 2.0 * u + 0.5 * x + rng.normal(size=n)
    df = pd.DataFrame({"t": t, "y": y, "x": x, "z": z})
    dag = {"edges": [("x", "t"), ("x", "y"), ("z", "t"), ("t", "y")]}

    estimates = await run_estimators(
        df,
        InterventionSpec(column="t", actual=1.0, counterfactual=0.0),
        OutcomeSpec(column="y", agg="mean", window=("2025-01-01", "2025-12-31")),
        dag, methods=["tmle"],
    )
    tmle = estimates[0]
    assert tmle.error is None, tmle.error

    # Plain regression of y on t, x and z -- what adjusting for the instrument amounts to.
    design = np.column_stack([np.ones(n), t, x, z])
    with_instrument = float(np.linalg.lstsq(design, y, rcond=None)[0][1])
    assert abs(tmle.point - 1.0) < abs(with_instrument - 1.0)
