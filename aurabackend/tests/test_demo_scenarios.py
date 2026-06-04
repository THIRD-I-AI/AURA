"""S31b — demo-scenario registry tests."""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.demo_scenarios import get_scenario, list_scenarios


def test_registry_lists_fair_lending():
    ids = [s["id"] for s in list_scenarios()]
    assert "fair_lending" in ids
    meta = next(s for s in list_scenarios() if s["id"] == "fair_lending")
    assert set(meta) >= {"id", "title", "vertical", "description"}


def test_get_scenario_builds_valid_query_and_df():
    sc = get_scenario("fair_lending")
    df = sc.build_dataset()
    q = sc.query()
    assert isinstance(df, pd.DataFrame) and len(df) > 0
    assert q.treatment.column in df.columns
    assert q.outcome.column in df.columns
    for src, dst in q.dag.edges:
        assert src in df.columns and dst in df.columns


def test_build_dataset_is_deterministic():
    sc = get_scenario("fair_lending")
    pd.testing.assert_frame_equal(sc.build_dataset(), sc.build_dataset())


def test_unknown_scenario_raises_keyerror():
    with pytest.raises(KeyError):
        get_scenario("nope")


# ── Parametrized over EVERY registered scenario ─────────────────────
# New scenarios are covered automatically — no per-scenario test needed.

_SCENARIO_IDS = [s["id"] for s in list_scenarios()]


@pytest.mark.parametrize("sid", _SCENARIO_IDS)
def test_scenario_builds_valid_query(sid):
    sc = get_scenario(sid)
    df = sc.build_dataset()
    q = sc.query()
    assert isinstance(df, pd.DataFrame) and len(df) > 0
    assert q.treatment.column in df.columns
    assert q.outcome.column in df.columns
    for src, dst in q.dag.edges:
        assert src in df.columns and dst in df.columns
    # IV identification contract: the declared instrument must be a real
    # column with an edge to the treatment and NO edge to the outcome.
    if sc.instrument is not None:
        assert sc.instrument in df.columns
        to_treatment = {s for s, d in q.dag.edges if d == q.treatment.column}
        to_outcome = {s for s, d in q.dag.edges if d == q.outcome.column}
        assert sc.instrument in to_treatment
        assert sc.instrument not in to_outcome


@pytest.mark.parametrize("sid", _SCENARIO_IDS)
def test_scenario_deterministic(sid):
    sc = get_scenario(sid)
    pd.testing.assert_frame_equal(sc.build_dataset(), sc.build_dataset())


@pytest.mark.parametrize("sid", _SCENARIO_IDS)
def test_scenario_estimators_agree_and_iv_valid(sid):
    """[Tier B] The curated backdoor estimators run cleanly on every scenario.
    For the instrument-bearing disparate-impact scenarios they also agree the
    treatment LOWERS the outcome and IV produces a valid bracketed CI. Instrument-
    free scenarios (e.g. the real-data COMPAS audit) only require the backdoor
    estimators to run — the effect's sign and significance are the honest open
    question there, not something to assert."""
    pytest.importorskip("econml")
    import asyncio

    from counterfactual_service.engine import run_estimators
    sc = get_scenario(sid)
    q = sc.query()
    has_iv = sc.instrument is not None
    methods = ["double_ml", "tmle"] + (["iv"] if has_iv else [])
    est = asyncio.run(run_estimators(
        sc.build_dataset(), q.treatment, q.outcome, q.dag.model_dump(),
        methods=methods, request_hash="t",
    ))
    by = {e.method: e for e in est}
    assert set(methods).issubset(by)
    errs = {m: by[m].error for m in by}
    assert all(by[m].error is None for m in methods), errs
    if has_iv:
        pts = [by[m].point for m in methods]
        assert all(p < 0 for p in pts), pts
        iv = by["iv"]
        assert iv.ci_lower < iv.point < iv.ci_upper


# ── COMPAS (real-data) scenario ─────────────────────────────────────

def test_compas_scenario_registered_with_real_subset():
    ids = [s["id"] for s in list_scenarios()]
    assert "compas_recidivism" in ids
    sc = get_scenario("compas_recidivism")
    df = sc.build_dataset()
    assert len(df) > 1000  # real ProPublica subset, not a tiny synthetic set
    q = sc.query()
    assert q.treatment.column == "african_american"
    assert q.outcome.column == "two_year_recid"
    assert sc.instrument is None
    assert set(df["african_american"].unique()) <= {0, 1}


def test_compas_narrative_does_not_overclaim_when_not_significant():
    sc = get_scenario("compas_recidivism")
    artifact = {"estimates": [
        {"point": -0.024, "ci_lower": -0.08, "ci_upper": 0.03, "error": None},
        {"point": -0.023, "ci_lower": -0.05, "ci_upper": 0.01, "error": None},
    ]}
    text = sc.narrative(artifact).lower()
    assert "not statistically significant" in text
    assert "biased" not in text  # never assert COMPAS is biased


def test_compas_narrative_reports_significance_when_ci_excludes_zero():
    sc = get_scenario("compas_recidivism")
    artifact = {"estimates": [
        {"point": 0.12, "ci_lower": 0.06, "ci_upper": 0.18, "error": None},
    ]}
    text = sc.narrative(artifact).lower()
    assert "significant" in text and "not statistically significant" not in text
