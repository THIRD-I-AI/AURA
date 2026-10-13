"""BUG-435: the cross-check must compare group keys too, not only the measures."""
from __future__ import annotations

import pandas as pd

from agents.dpc_verifier import results_agree

_DF = pd.DataFrame({
    "region": ["East", "East", "West"],
    "sales": [40.0, 60.0, 250.0],
    "day": pd.to_datetime(["2025-01-01", "2025-01-01", "2025-01-02"]),
})


def test_a_correct_group_by_agrees_with_the_grouped_series():
    sql_rows = [["East", 100.0], ["West", 250.0]]
    assert results_agree(["region", "total"], sql_rows, _DF.groupby("region")["sales"].sum())


def test_a_correct_group_by_agrees_with_the_grouped_frame():
    sql_rows = [["East", 100.0, 2], ["West", 250.0, 1]]
    grouped = _DF.groupby("region").agg(total=("sales", "sum"), n=("sales", "count"))
    assert results_agree(["region", "total", "n"], sql_rows, grouped)


def test_a_wrong_group_total_is_still_a_mismatch():
    sql_rows = [["East", 999.0], ["West", 250.0]]
    assert not results_agree(["region", "total"], sql_rows, _DF.groupby("region")["sales"].sum())


def test_a_filtered_frame_is_not_given_its_row_numbers():
    sql_rows = [["West", 250.0]]
    assert results_agree(["region", "sales"], sql_rows, _DF[_DF.sales > 100][["region", "sales"]])


def test_timestamps_compare_equal_across_the_two_serialisations():
    sql_rows = [["2025-01-01T00:00:00", 100.0], ["2025-01-02T00:00:00", 250.0]]
    assert results_agree(["day", "total"], sql_rows, _DF.groupby("day")["sales"].sum())
