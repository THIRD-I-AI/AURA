"""BUG-419: binarising a treatment must leave both a treated and a control group."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from counterfactual_service.audit_mapping import validate_and_prepare
from counterfactual_service.iv_estimator import WeakInstrumentError, first_stage_diagnostics


def _mapping() -> dict:
    return {"uploaded_file": "decisions.csv", "treatment": "t", "outcome": "y",
            "confounders": ["x"], "instrument": None}


def _frame(treatment) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = len(treatment)
    return pd.DataFrame({"t": treatment, "y": rng.normal(60, 5, n), "x": rng.normal(size=n)})


def test_a_zero_inflated_treatment_keeps_its_zeros_as_the_control_group():
    rng = np.random.default_rng(1)
    counts = np.where(rng.random(2000) < 0.6, 0, rng.integers(1, 6, 2000))
    clean, dq, _ = validate_and_prepare(_frame(counts), _mapping())
    assert sorted(clean["t"].unique()) == [0.0, 1.0]
    assert (clean["t"] == 0.0).sum() == (counts == 0).sum()
    assert any("above its median" in w for w in dq.warnings)


def test_a_treatment_spread_around_its_median_is_still_split_at_it():
    values = np.random.default_rng(2).normal(50, 10, 500)
    clean, dq, _ = validate_and_prepare(_frame(values), _mapping())
    assert (clean["t"] == 1.0).sum() == (values >= np.median(values)).sum()


def test_a_constant_treatment_is_refused():
    with pytest.raises(ValueError, match="same value in every usable row"):
        validate_and_prepare(_frame(np.ones(500)), _mapping())


def test_the_first_stage_refuses_a_treatment_that_does_not_vary():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"t": np.ones(500), "z": rng.integers(0, 2, 500).astype(float), "x": rng.normal(size=500)})
    with pytest.raises(WeakInstrumentError, match="does not vary"):
        first_stage_diagnostics(df, "t", ["z"], ["x"])
