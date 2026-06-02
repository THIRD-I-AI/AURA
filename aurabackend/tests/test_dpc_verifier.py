"""DPC dual-paradigm SQL verification — Tier A unit tests (LLM/tools faked)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.dpc_verifier import VerificationResult, results_agree


def test_verification_result_defaults():
    vr = VerificationResult(status="skipped", verified=None, reason="multi-table")
    assert vr.status == "skipped"
    assert vr.verified is None
    assert vr.method == "dual_paradigm_pandas"
    assert vr.pandas_expr is None


def test_results_agree_scalar_match():
    # SQL returned one cell = 6; pandas computed scalar 6 → agree.
    assert results_agree(["s"], [[6]], 6) is True


def test_results_agree_scalar_mismatch():
    assert results_agree(["s"], [[5]], 6) is False


def test_results_agree_within_tolerance():
    assert results_agree(["s"], [[1.0000001]], 1.0, tol=1e-6) is True


def test_results_agree_order_insensitive():
    # Row order differs between SQL and pandas — still the same value set.
    pandas_side = pd.DataFrame({"g": ["b", "a"], "v": [3, 1]})
    assert results_agree(["g", "v"], [["a", 1], ["b", 3]], pandas_side) is True


def test_results_agree_series_and_none():
    s = pd.Series([10, 20])
    assert results_agree(["c"], [[10], [20]], s) is True
    assert results_agree(["c"], [[None]], pd.Series([np.nan])) is True


from agents.dpc_verifier import extract_single_table


def test_extract_single_table_simple():
    assert extract_single_table('SELECT * FROM "sales"') == "sales"


def test_extract_single_table_join_returns_none():
    sql = 'SELECT a.x FROM "t1" a JOIN "t2" b ON a.id = b.id'
    assert extract_single_table(sql) is None


def test_extract_single_table_cte_excludes_alias():
    # CTE alias `c` is not a base table — the lone base table is `orders`.
    sql = 'WITH c AS (SELECT * FROM "orders") SELECT count(*) FROM c'
    assert extract_single_table(sql) == "orders"


def test_extract_single_table_no_table():
    assert extract_single_table("SELECT 1") is None


def test_extract_single_table_unparseable():
    assert extract_single_table("this is not sql ;;;") is None


import time as _time

from agents.dpc_verifier import _run_with_timeout, safe_eval_pandas


def _df():
    return pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3], "cost": [10, 20, 30]})


def test_safe_eval_pandas_correct():
    assert safe_eval_pandas('df["v"].sum()', _df()) == 6


def test_safe_eval_pandas_allows_columns_containing_denied_substrings():
    # "cost" contains "os" — must NOT be rejected (word-boundary denylist).
    assert safe_eval_pandas('df["cost"].sum()', _df()) == 60


@pytest.mark.parametrize("expr", [
    '__import__("os").system("echo hi")',
    "import os",
    'open("/etc/passwd").read()',
    "df.__class__.__mro__",
    "globals()",
    "os.getcwd()",
])
def test_safe_eval_pandas_rejects_dangerous(expr):
    with pytest.raises(ValueError):
        safe_eval_pandas(expr, _df())


def test_run_with_timeout_raises():
    with pytest.raises(TimeoutError):
        _run_with_timeout(lambda: _time.sleep(2.0), 0.2)
