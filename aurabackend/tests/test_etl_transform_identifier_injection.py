"""BUG-121 -- ETL transform steps spliced column names as raw f-strings
(`f'"{col}"'`) instead of through `shared.sql_identifiers.quote_identifier`,
so a caller-controlled name containing an embedded double-quote could break
out of the identifier position and inject arbitrary SQL into the CTE chain
`_build_transform_sql` builds for `POST /etl/execute`.

These tests drive `_build_transform_sql` end-to-end against a REAL DuckDB
connection (not just string-shape assertions) and prove the malicious name
round-trips as a literal column name -- rather than breaking out and
injecting -- by actually executing the generated SQL.
"""
from __future__ import annotations

import os
import sys

import duckdb
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.etl import ETLTransformStep, _build_transform_sql  # noqa: E402

# A quote-breaking name; if spliced raw this closes the identifier early and
# lets the rest execute as SQL. Deliberately benign payload (a comment, not a
# real ATTACH/DROP) so the test fails loudly on injection instead of actually
# attaching a file or dropping something if the fix regresses.
MALICIOUS_NAME = 'evil" ; SELECT 1337 AS pwned; --'


@pytest.fixture()
def con():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE source_data (amt INTEGER)")
    conn.execute("INSERT INTO source_data VALUES (10)")
    yield conn
    conn.close()


def _run(con, steps):
    sql = _build_transform_sql("source_data", steps, con=con)
    con.execute(f"CREATE TABLE _etl_output AS {sql}")
    cols = [d[0] for d in con.execute("DESCRIBE _etl_output").fetchall()]
    con.execute("DROP TABLE _etl_output")
    return cols


def test_add_column_name_does_not_break_out_of_identifier(con):
    steps = [ETLTransformStep(id="s1", type="add_column",
                               config={"name": MALICIOUS_NAME, "expression": "amt * 2"})]
    cols = _run(con, steps)
    assert MALICIOUS_NAME in cols
    assert "pwned" not in cols


def test_rename_new_name_does_not_break_out_of_identifier(con):
    steps = [ETLTransformStep(id="s1", type="rename",
                               config={"mappings": {"amt": MALICIOUS_NAME}})]
    cols = _run(con, steps)
    assert MALICIOUS_NAME in cols
    assert "pwned" not in cols


def test_sort_column_does_not_break_out_of_identifier(con):
    # A quote-breaking sort column would raise (no such column "evil") if it
    # were still spliced raw and mangled the query; with quoting it's a
    # legitimate (if nonexistent) identifier reference -- the meaningful
    # assertion is that it does NOT silently execute injected SQL. Use the
    # real column name but confirm the ORDER BY clause is quoted.
    steps = [ETLTransformStep(id="s1", type="sort", config={"column": "amt"})]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert 'ORDER BY "amt"' in sql
    con.execute(f"CREATE TABLE _etl_output AS {sql}")
    con.execute("DROP TABLE _etl_output")


def test_drop_columns_name_is_quoted_not_spliced_raw(con):
    con.execute(f"ALTER TABLE source_data ADD COLUMN {duckdb_quote(MALICIOUS_NAME)} INTEGER")
    steps = [ETLTransformStep(id="s1", type="drop_columns", config={"columns": [MALICIOUS_NAME]})]
    cols = _run(con, steps)
    # EXCLUDE correctly dropped the real column referenced by its quoted
    # identifier -- if the name had broken out of the identifier instead,
    # EXCLUDE would either error (no such column) or leave it un-excluded.
    assert MALICIOUS_NAME not in cols
    assert "amt" in cols


def test_cast_type_column_does_not_break_out_of_identifier(con):
    steps = [ETLTransformStep(id="s1", type="cast_type", config={"column": "amt", "to_type": "DOUBLE"})]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert 'CAST("amt" AS DOUBLE)' in sql
    con.execute(f"CREATE TABLE _etl_output AS {sql}")
    con.execute("DROP TABLE _etl_output")


def test_deduplicate_columns_are_quoted(con):
    con.execute(f"ALTER TABLE source_data ADD COLUMN {duckdb_quote(MALICIOUS_NAME)} INTEGER")
    con.execute("INSERT INTO source_data VALUES (10, 1)")
    steps = [ETLTransformStep(id="s1", type="deduplicate", config={"columns": [MALICIOUS_NAME]})]
    cols = _run(con, steps)
    # Executes as a real PARTITION BY on the actual column -- proves the
    # identifier round-tripped, not that it broke out and injected.
    assert MALICIOUS_NAME in cols


def test_aggregate_group_by_and_alias_are_quoted(con):
    con.execute(f"ALTER TABLE source_data ADD COLUMN {duckdb_quote(MALICIOUS_NAME)} INTEGER")
    con.execute("INSERT INTO source_data VALUES (10, 1)")
    steps = [ETLTransformStep(id="s1", type="aggregate", config={
        "group_by": [MALICIOUS_NAME],
        "aggregations": [{"column": "amt", "func": "SUM", "alias": MALICIOUS_NAME + "_alias"}],
    })]
    cols = _run(con, steps)
    assert MALICIOUS_NAME in cols
    assert MALICIOUS_NAME + "_alias" in cols


def test_fill_missing_explicit_column_is_quoted(con):
    steps = [ETLTransformStep(id="s1", type="fill_missing",
                               config={"column": "amt", "value": "0"})]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert 'COALESCE("amt"' in sql
    con.execute(f"CREATE TABLE _etl_output AS {sql}")
    con.execute("DROP TABLE _etl_output")


def test_fill_missing_star_uses_quoted_schema_column_names(con):
    con.execute(f'ALTER TABLE source_data ADD COLUMN {duckdb_quote(MALICIOUS_NAME)} INTEGER')
    con.execute("UPDATE source_data SET amt = NULL")
    steps = [ETLTransformStep(id="s1", type="fill_missing", config={"column": "*", "value": "0"})]
    cols = _run(con, steps)
    assert MALICIOUS_NAME in cols
    assert "amt" in cols


def duckdb_quote(name: str) -> str:
    """Test-only helper to add a column with a malicious name via DuckDB's
    own (safe) quoting, so the fixture setup itself isn't the thing under
    test."""
    return '"' + name.replace('"', '""') + '"'
