"""Audit-your-own-data — column-mapping helpers (Tier A, pure)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.audit_mapping import build_dag_from_mapping


def _mapping(instrument=None):
    return {"uploaded_file": "decisions.csv", "treatment": "t", "outcome": "y",
            "confounders": ["x"], "instrument": instrument}


# ── build_dag_from_mapping ──────────────────────────────────────────

def test_dag_backdoor_edges_no_instrument():
    dag = build_dag_from_mapping("flag", "approved", ["income", "dti"], None)
    edges = set(dag.edges)
    assert ("income", "flag") in edges and ("income", "approved") in edges
    assert ("dti", "flag") in edges and ("dti", "approved") in edges
    assert ("flag", "approved") in edges
    assert not any(e[1] == "flag" and e[0] not in ("income", "dti") for e in edges)


def test_dag_includes_instrument_edge():
    dag = build_dag_from_mapping("flag", "approved", ["income"], "officer")
    assert ("officer", "flag") in set(dag.edges)
    assert ("officer", "approved") not in set(dag.edges)  # exclusion restriction


def test_dag_rejects_self_loop_when_confounder_equals_treatment():
    with pytest.raises(Exception):
        build_dag_from_mapping("flag", "approved", ["flag"], None)


# ── validate_and_prepare ────────────────────────────────────────────

from counterfactual_service.audit_mapping import DataQuality, validate_and_prepare  # noqa: E402


def test_validate_missing_column_raises():
    df = pd.DataFrame({"t": [0, 1], "y": [1, 0]})  # no 'x'
    with pytest.raises(ValueError) as e:
        validate_and_prepare(df, _mapping())
    assert "x" in str(e.value)


def test_validate_drops_nan_rows_and_counts():
    rng = np.random.default_rng(0)
    n = 200
    df = pd.DataFrame({"t": rng.integers(0, 2, n), "y": rng.integers(0, 2, n),
                       "x": rng.normal(size=n)})
    df.loc[:9, "x"] = np.nan  # 10 missing
    clean, dq = validate_and_prepare(df, _mapping())
    assert dq.n_dropped == 10 and dq.n_clean == n - 10
    assert clean["x"].isna().sum() == 0
    assert isinstance(dq, DataQuality)


def test_validate_too_few_rows_raises():
    df = pd.DataFrame({"t": [0, 1, 0], "y": [1, 0, 1], "x": [0.1, 0.2, 0.3]})
    with pytest.raises(ValueError) as e:
        validate_and_prepare(df, _mapping())
    assert "rows" in str(e.value).lower()


def test_validate_binarises_continuous_treatment_and_flags():
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame({"t": rng.normal(size=n), "y": rng.integers(0, 2, n),
                       "x": rng.normal(size=n)})
    clean, dq = validate_and_prepare(df, _mapping())
    assert set(clean["t"].unique()) <= {0.0, 1.0}
    assert dq.treatment_is_binary is False
    assert any("binaris" in w.lower() for w in dq.warnings)
