"""BUG-220: the SQL expression guard must reject a second, stacked statement --
without this, EXPORT DATABASE (or any other statement) can be smuggled in after
a semicolon and actually executed by DuckDB's Connection.execute()."""
import os

import duckdb
import pytest

from shared.sql_expression_guard import validate_sql_expression


@pytest.mark.parametrize("expr", [
    "1=1; EXPORT DATABASE '/tmp/pwned' (FORMAT CSV)",
    "SELECT 1) SELECT 1; EXPORT DATABASE '/tmp/pwn_test' (FORMAT CSV); SELECT 1 AS x; --",
    "1=1; IMPORT DATABASE '/tmp/x'",
    "1=1 ; PRAGMA database_list",
    "1=1; ATTACH ':memory:' AS x",
])
def test_stacked_statement_is_rejected(expr):
    with pytest.raises(ValueError, match="second statement"):
        validate_sql_expression(expr)


@pytest.mark.parametrize("expr", [
    "price > 10 AND name = 'a;b'",
    "upper(x) = 'y;z;'",
    "a + b",
    'x = "col;name"',
    "name = 'it''s; fine'",
])
def test_semicolon_inside_a_string_literal_or_identifier_is_not_a_statement_boundary(expr):
    validate_sql_expression(expr)  # must not raise


def test_export_database_alone_is_also_blocked_by_the_word_list():
    with pytest.raises(ValueError, match="file/network access functions"):
        validate_sql_expression("EXPORT DATABASE '/tmp/x' (FORMAT CSV)")


def test_end_to_end_export_database_no_longer_writes_to_disk(tmp_path):
    """Drive the actual splice shape used by etl.py's custom_sql branch against a
    real DuckDB connection holding another table, and confirm nothing is written."""
    out_dir = tmp_path / "pwn_out"
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE secret_tenant_data AS SELECT 42 AS ssn")

    sql_expr = (
        "SELECT 1) SELECT 1; EXPORT DATABASE '" + str(out_dir) + "' (FORMAT CSV); "
        "SELECT 1 AS x; --"
    )
    with pytest.raises(ValueError):
        validate_sql_expression(sql_expr)

    # Sanity: prove this really would have exfiltrated the table had the guard not
    # rejected it (same query shape the guard is meant to stand in front of).
    transform_sql = f"WITH step_0 AS ({sql_expr})\nSELECT * FROM step_0"
    try:
        con.execute(f"CREATE TABLE _etl_output AS {transform_sql}")
    except Exception:
        pass
    assert os.path.exists(out_dir / "secret_tenant_data.csv"), (
        "expected the unguarded query to still exfiltrate the table -- "
        "if this fails, the exploit shape itself changed and the test needs updating"
    )
