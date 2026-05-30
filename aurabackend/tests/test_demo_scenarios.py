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
