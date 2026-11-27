"""BUG-599: shared/sql_expression_guard.py's keyword scan was bypassable -- a quoted
function name (``"read_text"(...)``), a dollar-quoted string that hides a stacked
``;``/CALL, and a no-paren or stored-view introspection call all slipped past it. The
guard now parses the expression with DuckDB's own parser (``json_serialize_sql``),
accepts only a single read-only query, and refuses file/network, scanner, dynamic-SQL,
logging and path-leaking introspection functions. The keyword scan stays as the
no-parser fallback; the locked DuckDB connection is the real control.

The BYPASS cases below pass the old keyword guard (so they fail on origin/main) and are
refused here. The LEGIT cases are ordinary analytic SQL that must keep working.
"""
from __future__ import annotations

import pytest

from shared.sql_expression_guard import validate_sql_expression

SECRET_PATH = "/data/keys/signing_ed25519.pem"

# Each of these slips past the pre-BUG-599 keyword scan; the parser refuses them.
BYPASSES = {
    "quoted file reader": "SELECT content FROM \"read_text\"('/etc/passwd')",
    "quoted introspection": "SELECT \"current_setting\"('secret_directory') AS s",
    "quoted sqlite scanner": "SELECT * FROM \"sqlite_scan\"('/data/state/metadata.db', 'users')",
    "no-paren duckdb_databases": "SELECT database_name, path FROM duckdb_databases",
    "no-paren pragma_database_list": "SELECT * FROM pragma_database_list",
    "duckdb_settings view": "SELECT name, value FROM duckdb_settings() WHERE name = 'secret_directory'",
    "getenv": "SELECT getenv('AURA_CREDENTIAL_ENCRYPTION_KEY')",
    "enable_logging table function": "SELECT * FROM enable_logging(storage=>'file', storage_path=>'/tmp/x')",
    "dollar-quote hides stacked CALL":
        "SELECT $$\"$$ AS a); CALL enable_logging(); SELECT * FROM (SELECT $$\"$$ AS a",
    "dollar-quote hides a file read":
        "SELECT $$'$$ AS a, content FROM read_text($$/etc/passwd$$) WHERE '' <> $$'$$",
    "query() dynamic sql": "SELECT * FROM query('SELECT * FROM read_text(''/etc/passwd'')')",
    "pragma_platform leak": "SELECT version() AS v, * FROM pragma_platform()",
}

# Replacement scans and classic readers the keyword scan already caught -- must stay refused.
STILL_REFUSED = {
    "read_csv_auto": "SELECT * FROM read_csv_auto('/etc/passwd')",
    "replacement scan single quote": "SELECT * FROM '/etc/passwd'",
    "replacement scan double quote": 'SELECT * FROM "/etc/passwd"',
    "glob": "SELECT * FROM glob('/data/keys/*')",
    "stacked statement": "SELECT 1; DROP TABLE t",
    "sql comment": "amount > 0 /* note */",
}

# Ordinary analytic SQL that must keep passing (no false refusals).
LEGIT = {
    "bare condition": "amount > 100",
    "computed column": "a + b * 2",
    "coalesce": "COALESCE(discount, 0)",
    "like with keyword in a literal": "lower(note) LIKE '%please attach and copy%'",
    "aggregate select": "SELECT region, SUM(amount) AS total, COUNT(*) AS n FROM src GROUP BY region",
    "cte": "WITH c AS (SELECT * FROM src WHERE amount > 0) SELECT * FROM c ORDER BY amount DESC",
    "window function": "SELECT region, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) rn FROM src",
    "join and union": "SELECT * FROM a JOIN b USING (k) UNION ALL SELECT * FROM c",
    "column named like a keyword": "SELECT database_id, copy_count, load_ts FROM t WHERE database_id = 5",
    "template placeholder": "SELECT * FROM {{prev}} WHERE status = 'copy'",
    "etl input placeholder": "SELECT * FROM {{input}} WHERE note = 'export me'",
    "string with semicolon": "name = 'a; b; c'",
}


@pytest.mark.parametrize("expr", list(BYPASSES.values()), ids=list(BYPASSES))
def test_guard_bypasses_are_refused(expr):
    with pytest.raises(ValueError):
        validate_sql_expression(expr)


@pytest.mark.parametrize("expr", list(STILL_REFUSED.values()), ids=list(STILL_REFUSED))
def test_classic_vectors_stay_refused(expr):
    with pytest.raises(ValueError):
        validate_sql_expression(expr)


@pytest.mark.parametrize("expr", list(LEGIT.values()), ids=list(LEGIT))
def test_legitimate_sql_still_passes(expr):
    validate_sql_expression(expr)  # must not raise


def test_a_refusal_never_echoes_the_offending_file_path():
    """BUG-189/BUG-284: a replacement-scan 'table' is the caller's own path; the error
    must not carry it back."""
    try:
        validate_sql_expression(f"SELECT * FROM '{SECRET_PATH}'")
    except ValueError as exc:
        assert SECRET_PATH not in str(exc)
        assert "signing_ed25519" not in str(exc)
    else:  # pragma: no cover - the line above must raise
        pytest.fail("a replacement scan must be refused")


def test_keyword_fallback_still_refuses_when_the_parser_is_unavailable(monkeypatch):
    """With DuckDB un-importable, the guard must fall back to the keyword scan rather
    than accept everything."""
    import builtins

    import shared.sql_expression_guard as guard

    real_import = builtins.__import__

    def no_duckdb(name, *args, **kwargs):
        if name == "duckdb":
            raise ImportError("duckdb unavailable (test)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(guard, "_parse_con", None)
    monkeypatch.setattr(builtins, "__import__", no_duckdb)
    with pytest.raises(ValueError):
        validate_sql_expression("SELECT * FROM read_csv_auto('/etc/passwd')")
