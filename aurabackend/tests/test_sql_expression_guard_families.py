"""BUG-207: the guard must block DuckDB's file-reading table-function *families*,
not just the handful of names it used to list."""
import pytest

from shared.sql_expression_guard import validate_sql_expression


@pytest.mark.parametrize("expr", [
    "(SELECT * FROM read_json_objects('/etc/passwd'))",
    "(SELECT * FROM read_ndjson_objects('/data/x.json'))",
    "x IN (SELECT a FROM parquet_scan('/data/uploads/other-tenant/x.parquet'))",
    "(SELECT * FROM sniff_csv('/data/x.csv'))",
    "x IN (SELECT a FROM parquet_metadata('/data/x.parquet'))",
    "x IN (SELECT a FROM parquet_schema('/data/x.parquet'))",
    "a + (SELECT count(*) FROM READ_XLSX ('/data/x.xlsx'))",
    "(SELECT * FROM delta_scan('/data/t'))",
    "(SELECT * FROM iceberg_scan('/data/t'))",
    "(SELECT * FROM st_read('/data/x.shp'))",
    "(SELECT * FROM query('select 1'))",
    "(SELECT * FROM query_table('t'))",
    "(SELECT value FROM duckdb_settings())",
    "getenv('HOME') = 'x'",
])
def test_file_reading_and_dynamic_sql_functions_are_blocked(expr):
    with pytest.raises(ValueError):
        validate_sql_expression(expr)


@pytest.mark.parametrize("expr", [
    "price > 10 AND read_count < 5",
    "upper(name) = 'A'",
    "a * 2 + b",
    "date_trunc('day', ts) > DATE '2024-01-01'",
    "coalesce(x, 0) > 3",
    "last_scan IS NOT NULL",
])
def test_ordinary_expressions_and_similarly_named_columns_still_pass(expr):
    validate_sql_expression(expr)
