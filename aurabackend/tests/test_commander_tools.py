"""Task 2 — the commander tool registry + guarded run_sql.

The run_sql guard is intentionally STRONGER than a keyword blocklist: a
sqlglot parse that requires exactly one statement of SELECT/CTE *type*, so it
rejects DuckDB-specific escapes (ATTACH/COPY/PRAGMA/INSTALL) and multi-statement
injection structurally — not just the common DDL keywords. Tools never raise to
the loop; every failure is a ToolOutcome(ok=False)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.commander_tools import ToolOutcome, build_default_registry

duckdb = pytest.importorskip("duckdb")


def _con():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE sales (id INTEGER, amount INTEGER)")
    con.execute("INSERT INTO sales VALUES (1, 10), (2, 20), (3, 30)")
    return con


def _tables(con):
    return {r[0] for r in con.execute("SHOW TABLES").fetchall()}


def test_run_sql_returns_rows():
    reg = build_default_registry()
    out = reg.execute("run_sql", {"sql": "SELECT amount FROM sales ORDER BY amount"},
                      tenant="t1", con=_con())
    assert isinstance(out, ToolOutcome)
    assert out.ok is True
    assert out.value["row_count"] == 3
    assert out.value["columns"] == ["amount"]
    assert out.value["rows"][0] == [10]


def test_run_sql_allows_cte():
    reg = build_default_registry()
    out = reg.execute("run_sql",
                      {"sql": "WITH t AS (SELECT amount FROM sales) SELECT sum(amount) AS s FROM t"},
                      tenant="t1", con=_con())
    assert out.ok is True
    assert out.value["rows"][0] == [60]


def test_run_sql_rejects_ddl():
    reg = build_default_registry()
    con = _con()
    out = reg.execute("run_sql", {"sql": "DROP TABLE sales"}, tenant="t1", con=con)
    assert out.ok is False
    assert out.error
    assert "sales" in _tables(con)  # destructive statement did not run


def test_run_sql_rejects_multi_statement_injection():
    reg = build_default_registry()
    con = _con()
    out = reg.execute("run_sql", {"sql": "SELECT 1; CREATE TABLE pwned AS SELECT 1"},
                      tenant="t1", con=con)
    assert out.ok is False
    assert "pwned" not in _tables(con)


def test_run_sql_rejects_attach():
    # ATTACH is a DuckDB escape a keyword blocklist would MISS — the
    # type-based parse must reject it.
    reg = build_default_registry()
    con = _con()
    out = reg.execute("run_sql", {"sql": "ATTACH 'evil.db' AS evil"}, tenant="t1", con=con)
    assert out.ok is False


def test_run_sql_rejects_copy_and_pragma():
    reg = build_default_registry()
    con = _con()
    for sql in ("COPY sales TO 'leak.csv'", "PRAGMA database_list"):
        out = reg.execute("run_sql", {"sql": sql}, tenant="t1", con=con)
        assert out.ok is False, f"should reject: {sql}"


def test_run_sql_enforces_row_limit():
    reg = build_default_registry()
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE big AS SELECT * FROM range(5000) t(n)")
    out = reg.execute("run_sql", {"sql": "SELECT n FROM big"}, tenant="t1", con=con)
    assert out.ok is True
    assert out.value["row_count"] <= 1000


def test_unknown_tool_returns_error_not_raise():
    reg = build_default_registry()
    out = reg.execute("does_not_exist", {}, tenant="t1", con=_con())
    assert out.ok is False
    assert "does_not_exist" in out.error


def test_missing_required_arg_returns_error_not_raise():
    reg = build_default_registry()
    out = reg.execute("run_sql", {}, tenant="t1", con=_con())
    assert out.ok is False
    assert out.error


def test_specs_shape_is_model_ready():
    reg = build_default_registry()
    specs = reg.specs()
    spec = next(s for s in specs if s["name"] == "run_sql")
    assert spec["parameters"]["required"] == ["sql"]
