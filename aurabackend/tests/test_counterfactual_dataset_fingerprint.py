"""BUG-297: the dataset fingerprint hashed only the first and last three rows, so a
dataset edited anywhere else kept its fingerprint -- and with it the request hash and
the critic cache key."""
from __future__ import annotations

import numpy as np
import pandas as pd

from counterfactual_service.engine import _dataset_fingerprint


def _df(n: int = 1000) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    return pd.DataFrame({"x": rng.normal(size=n), "t": rng.integers(0, 2, n), "label": ["a", "b"] * (n // 2)})


def test_a_changed_value_in_the_middle_changes_the_fingerprint():
    df = _df()
    edited = df.copy()
    edited.loc[500, "x"] += 1.0

    assert _dataset_fingerprint(edited) != _dataset_fingerprint(df)


def test_a_changed_string_in_the_middle_changes_the_fingerprint():
    df = _df()
    edited = df.copy()
    edited.loc[400, "label"] = "z"

    assert _dataset_fingerprint(edited) != _dataset_fingerprint(df)


def test_the_same_data_fingerprints_identically_whatever_the_column_order():
    df = _df()

    assert _dataset_fingerprint(df.copy()) == _dataset_fingerprint(df)
    assert _dataset_fingerprint(df[["label", "t", "x"]]) == _dataset_fingerprint(df)
    assert len(_dataset_fingerprint(df)) == 64


def test_columns_holding_unhashable_values_still_fingerprint():
    df = pd.DataFrame({"a": [1, 2, 3], "tags": [["x"], ["y", "z"], []]})
    edited = df.copy()
    edited.at[1, "tags"] = ["changed"]

    assert _dataset_fingerprint(edited) != _dataset_fingerprint(df)


def test_an_empty_frame_fingerprints():
    assert len(_dataset_fingerprint(pd.DataFrame({"a": []}))) == 64
