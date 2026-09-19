"""BUG-122 -- the ETL `aggregate` transform step spliced `agg["func"]`
(the aggregate function name, e.g. SUM/AVG) directly into a function-call
position with no allowlist or guard at all. Unlike BUG-121's column-name
splices, `quote_identifier` can't protect a function-call position, so this
needs an allowlist of the fixed set of real aggregate functions instead.
"""
from __future__ import annotations

import os
import sys

import duckdb
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.etl import ETLTransformStep, _build_transform_sql  # noqa: E402


@pytest.fixture()
def con():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE source_data (grp INTEGER, amt INTEGER)")
    conn.execute("INSERT INTO source_data VALUES (1, 10), (1, 20), (2, 5)")
    yield conn
    conn.close()


def test_valid_agg_func_is_allowed(con):
    steps = [ETLTransformStep(id="s1", type="aggregate", config={
        "group_by": ["grp"],
        "aggregations": [{"column": "amt", "func": "SUM", "alias": "total"}],
    })]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert "SUM(" in sql
    con.execute(f"CREATE TABLE _etl_output AS {sql}")
    rows = dict(con.execute("SELECT grp, total FROM _etl_output ORDER BY grp").fetchall())
    assert rows == {1: 30, 2: 5}


def test_malicious_func_is_rejected_not_spliced():
    """The exact vector: a func value that injects arbitrary SQL text into
    the function-call position rather than calling a real aggregate."""
    steps = [ETLTransformStep(id="s1", type="aggregate", config={
        "group_by": ["grp"],
        "aggregations": [{
            "column": "amt",
            "func": "(SELECT column0 FROM read_csv_auto('/etc/passwd')) - SUM",
            "alias": "leaked",
        }],
    })]
    sql = _build_transform_sql("source_data", steps)
    # No aggregations passed the allowlist -> the whole aggregate step is
    # skipped, same as a missing column/func -- the malicious text must
    # never reach the generated SQL.
    assert "read_csv_auto" not in sql
    assert sql == 'SELECT * FROM "source_data"'


def test_unknown_func_name_is_rejected():
    steps = [ETLTransformStep(id="s1", type="aggregate", config={
        "group_by": ["grp"],
        "aggregations": [{"column": "amt", "func": "NOT_A_REAL_FUNC", "alias": "x"}],
    })]
    sql = _build_transform_sql("source_data", steps)
    assert "NOT_A_REAL_FUNC" not in sql
    assert sql == 'SELECT * FROM "source_data"'


def test_func_name_is_case_and_whitespace_normalized(con):
    steps = [ETLTransformStep(id="s1", type="aggregate", config={
        "group_by": ["grp"],
        "aggregations": [{"column": "amt", "func": " sum ", "alias": "total"}],
    })]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert "SUM(" in sql
    con.execute(f"CREATE TABLE _etl_output AS {sql}")
    con.execute("DROP TABLE _etl_output")


def test_mixed_valid_and_malicious_aggregations_keeps_only_the_valid_one(con):
    steps = [ETLTransformStep(id="s1", type="aggregate", config={
        "group_by": ["grp"],
        "aggregations": [
            {"column": "amt", "func": "SUM", "alias": "total"},
            {"column": "amt", "func": "ATTACH 'evil.db' AS z; SELECT", "alias": "leaked"},
        ],
    })]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert "SUM(" in sql
    assert "ATTACH" not in sql
    con.execute(f"CREATE TABLE _etl_output AS {sql}")
    cols = [d[0] for d in con.execute("DESCRIBE _etl_output").fetchall()]
    assert "total" in cols
    assert "leaked" not in cols
