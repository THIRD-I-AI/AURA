"""S31b — IV (2SLS) estimator tests."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.iv_estimator import instruments_from_dag, run_iv_2sls


def test_instruments_from_dag_picks_node_to_treatment_not_outcome():
    edges = [("z", "t"), ("x", "t"), ("x", "y"), ("t", "y")]
    insts = instruments_from_dag(edges, treatment="t", outcome="y")
    assert insts == ["z"]   # x is a confounder (also -> y); t is treatment


def test_run_iv_2sls_recovers_known_effect():
    rng = np.random.default_rng(0)
    n = 4000
    z = rng.integers(0, 2, n).astype(float)          # instrument
    u = rng.normal(0, 1, n)                            # unobserved confounder
    t = (0.5 * z + 0.7 * u + rng.normal(0, 0.3, n) > 0.6).astype(float)
    y = 2.0 * t + 1.5 * u + rng.normal(0, 0.3, n)      # true effect of t on y is 2.0
    df = pd.DataFrame({"z": z, "t": t, "y": y})
    point, lo, hi = run_iv_2sls(df, treatment="t", outcome="y", instruments=["z"], confounders=[])
    # OLS of y~t would be biased upward by u; IV should be near 2.0.
    assert 1.4 < point < 2.6
    assert lo < point < hi


def test_engine_dispatch_iv_via_run_one_estimator():
    from counterfactual_service.engine import _run_one_estimator
    from counterfactual_service.schemas import InterventionSpec, OutcomeSpec
    rng = np.random.default_rng(1)
    n = 2000
    z = rng.integers(0, 2, n).astype(float)
    u = rng.normal(0, 1, n)
    t = (0.6 * z + 0.6 * u > 0.5).astype(float)
    y = 1.0 * t + 1.2 * u + rng.normal(0, 0.3, n)
    df = pd.DataFrame({"z": z, "t": t, "y": y})
    est = _run_one_estimator(
        "iv", df,
        InterventionSpec(column="t", actual=1, counterfactual=0),
        OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        {"edges": [["z", "t"], ["t", "y"]]}, seed=0,
    )
    assert est.method == "iv"
    assert est.error is None
    assert est.ci_lower < est.point < est.ci_upper


def test_run_job_honours_methods_list():
    dowhy = pytest.importorskip("dowhy")  # noqa: F841
    import asyncio

    from counterfactual_service.engine import run_job
    from counterfactual_service.schemas import (
        CounterfactualQuery,
        DAGSpec,
        DatasetRef,
        InterventionSpec,
        OutcomeSpec,
    )
    rng = np.random.default_rng(2)
    n = 800
    z = rng.integers(0, 2, n).astype(float)
    x = rng.normal(0, 1, n)
    t = ((0.5 * z + 0.5 * x) > 0.3).astype(float)
    y = (1.0 * t + 0.8 * x + rng.normal(0, 0.3, n))
    df = pd.DataFrame({"z": z, "x": x, "t": t, "y": y})
    q = CounterfactualQuery(
        question="effect of t on y",
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        dag=DAGSpec(edges=[("z", "t"), ("x", "t"), ("x", "y"), ("t", "y")]),
        dataset=DatasetRef(source_id="inline"),
    )
    art = asyncio.run(run_job(q, df=df, methods=["linear_regression", "iv"]))
    got = {e.method for e in art.estimates}
    assert got == {"linear_regression", "iv"}
