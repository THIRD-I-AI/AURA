"""Auto-encoding categorical columns for /audit (#49) — Tier A (pure pandas)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.audit_mapping import encode_for_audit, validate_and_prepare


def _df():
    return pd.DataFrame({
        "race": ["African-American", "Caucasian", "African-American", "Caucasian"],
        "sex": ["Male", "Female", "Male", "Female"],
        "recid": [1, 0, 1, 0],
        "priors": [3, 1, 2, 0],
    })


def test_binary_string_treatment_encoded_to_0_1():
    df, eff, warns = encode_for_audit(_df(), {"treatment": "race", "outcome": "recid", "confounders": ["priors"]})
    # race = [AA, Caucasian, AA, Caucasian]; sorted-first (AA) -> 0, other -> 1
    assert df["race"].tolist() == [0.0, 1.0, 0.0, 1.0]
    assert eff["treatment"] == "race"
    assert any("race" in w and "Caucasian=1" in w for w in warns)


def test_more_than_two_category_treatment_raises():
    d = _df()
    d.loc[0, "race"] = "Hispanic"  # 3 categories
    with pytest.raises(ValueError, match=r"two groups|reference|categories"):
        encode_for_audit(d, {"treatment": "race", "outcome": "recid", "confounders": ["priors"]})


def test_categorical_confounder_one_hot_drop_first():
    df, eff, warns = encode_for_audit(_df(), {"treatment": "race", "outcome": "recid", "confounders": ["sex", "priors"]})
    # sex (Female/Male) -> drop-first keeps one dummy
    assert "sex" not in df.columns
    assert "sex" not in eff["confounders"]
    dummy = [c for c in eff["confounders"] if c.startswith("sex_")]
    assert len(dummy) == 1 and dummy[0] in df.columns
    assert "priors" in eff["confounders"]  # numeric untouched
    assert pd.api.types.is_numeric_dtype(df[dummy[0]])


def test_multi_category_confounder_within_cap_one_hot():
    d = _df()
    d["charge"] = ["F", "M", "O", "F"]  # 3 categories
    df, eff, warns = encode_for_audit(d, {"treatment": "race", "outcome": "recid", "confounders": ["charge"]})
    dummies = [c for c in eff["confounders"] if c.startswith("charge_")]
    assert len(dummies) == 2  # 3 cats, drop-first
    assert "charge" not in df.columns


def test_high_cardinality_confounder_raises():
    d = _df()
    d["zip"] = [f"z{i}" for i in range(len(d))]  # all distinct
    with pytest.raises(ValueError, match=r"categories|bucket|cardinalit"):
        encode_for_audit(d, {"treatment": "race", "outcome": "recid", "confounders": ["zip"]}, card_cap=2)


def test_numeric_inputs_unchanged_no_warnings():
    d = pd.DataFrame({"t": [0, 1, 0, 1], "y": [1, 0, 1, 0], "x": [3, 1, 2, 0]})
    df, eff, warns = encode_for_audit(d, {"treatment": "t", "outcome": "y", "confounders": ["x"]})
    assert eff["confounders"] == ["x"]
    assert warns == []
    pd.testing.assert_frame_equal(df[["t", "y", "x"]].astype(float), d.astype(float))


def test_missing_categorical_rows_become_nan_for_downstream_dropna():
    d = _df()
    d.loc[0, "sex"] = np.nan
    df, eff, warns = encode_for_audit(d, {"treatment": "race", "outcome": "recid", "confounders": ["sex"]})
    dummy = [c for c in eff["confounders"] if c.startswith("sex_")][0]
    # NaN categorical must propagate to NaN dummy so the later dropna removes it.
    assert df[dummy].isna().sum() == 1


def test_effective_mapping_flows_into_dag():
    # The encoded confounder dummies (not the original string column) must be the
    # ones the auto-DAG adjusts on.
    from counterfactual_service.audit_mapping import build_query_from_mapping
    rng = np.random.default_rng(0)
    n = 150
    d = pd.DataFrame({
        "race": rng.choice(["African-American", "Caucasian"], n),
        "sex": rng.choice(["Male", "Female"], n),
        "approved": rng.integers(0, 2, n),
        "priors": rng.integers(0, 10, n),
    })
    clean, dq, eff = validate_and_prepare(
        d, {"treatment": "race", "outcome": "approved", "confounders": ["sex", "priors"], "uploaded_file": "x.csv"}
    )
    q = build_query_from_mapping(clean, eff)
    edges = set(q.dag.edges)
    dummy = [c for c in eff["confounders"] if c.startswith("sex_")][0]
    assert (dummy, "race") in edges and (dummy, "approved") in edges
    assert ("priors", "race") in edges
    assert any("one-hot" in w for w in dq.warnings)


def test_string_race_and_categorical_confounder_audit_end_to_end(tmp_path, monkeypatch):
    """Tier B: a raw CSV with string `race` + string `sex` confounder audits to a
    SIGNED artifact with no manual pre-encoding (the #49 headline)."""
    pytest.importorskip("econml")
    monkeypatch.chdir(tmp_path)
    up = tmp_path / "data" / "uploads"
    up.mkdir(parents=True)
    rng = np.random.default_rng(3)
    n = 400
    sex = rng.choice(["Male", "Female"], n)
    race = rng.choice(["African-American", "Caucasian"], n)
    score = rng.normal(0, 1, n)
    t = (race == "Caucasian").astype(int)
    y = 0.5 + 0.3 * score - 0.4 * t + (sex == "Male") * 0.1 + rng.normal(0, 0.3, n)
    pd.DataFrame({"race": race, "sex": sex, "approved": y, "score": score}).to_csv(up / "d.csv", index=False)

    from counterfactual_service.audit_worker import run_audit_subprocess
    res = run_audit_subprocess({
        "uploaded_file": "d.csv", "treatment": "race", "outcome": "approved",
        "confounders": ["sex", "score"], "instrument": None,
    })
    assert res["signature_status"] == "signed"
    assert res["data_quality"]["n_clean"] > 100
    assert any("one-hot" in w or "encoded" in w for w in res["data_quality"]["warnings"])
