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
    """[Tier B] Every scenario's curated estimators agree the flag/tier LOWERS
    the outcome, and IV (leniency instrument) produces a valid bracketed CI."""
    pytest.importorskip("econml")
    import asyncio

    from counterfactual_service.engine import run_estimators
    sc = get_scenario(sid)
    q = sc.query()
    est = asyncio.run(run_estimators(
        sc.build_dataset(), q.treatment, q.outcome, q.dag.model_dump(),
        methods=["double_ml", "tmle", "iv"], request_hash="t",
    ))
    by = {e.method: e for e in est}
    assert {"double_ml", "tmle", "iv"}.issubset(by)
    errs = {m: by[m].error for m in by}
    assert all(by[m].error is None for m in ("double_ml", "tmle", "iv")), errs
    pts = [by[m].point for m in ("double_ml", "tmle", "iv")]
    assert all(p < 0 for p in pts), pts
    iv = by["iv"]
    assert iv.ci_lower < iv.point < iv.ci_upper
