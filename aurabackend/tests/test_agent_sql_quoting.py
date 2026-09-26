"""BUG-199 / BUG-200: the quality and optimization agents built SQL from names and values that
come from uploaded CSV headers or LLM output, without the shared identifier quoter."""
from __future__ import annotations

import duckdb
import pytest

from agents.specialists.optimization_agent import OptimizationAgent
from agents.specialists.quality_agent import QualityAgent
from shared.sql_identifiers import quote_identifier

# A double quote in a header closes a bare-quoted identifier. The tail would create a table if it ran.
EVIL_COL = 'a" FROM t; CREATE TABLE pwned AS SELECT 1; --'
EVIL_TABLE = 't"; CREATE TABLE pwned2 AS SELECT 1; --'


def _agent():
    return QualityAgent.__new__(QualityAgent)  # _build_checks uses no instance state


def test_quality_checks_run_and_do_not_inject_through_column_or_table_names():
    con = duckdb.connect(":memory:")
    qt, qc = quote_identifier(EVIL_TABLE), quote_identifier(EVIL_COL)
    con.execute(f'CREATE TABLE {qt} ({qc} INTEGER, "id" INTEGER)')
    con.execute(f"INSERT INTO {qt} VALUES (1, 1), (NULL, 2)")

    checks = _agent()._build_checks({EVIL_TABLE: [EVIL_COL, "id"]}, {}, "")
    assert checks
    for c in checks:
        # Each generated statement must be a single valid query (no error, no second statement).
        rows = con.execute(c.sql).fetchall()
        assert len(rows) == 1

    names = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert "pwned" not in names and "pwned2" not in names, "a name broke out of its identifier and ran DDL"


def test_optimization_create_index_quotes_identifiers_and_still_runs():
    con = duckdb.connect(":memory:")
    con.execute('CREATE TABLE "o""rders" ("we""ird" INTEGER, b INTEGER)')
    sql = OptimizationAgent._build_create_index({"table": 'o"rders', "columns": ['we"ird', "b"], "type": "art"})
    assert sql is not None
    con.execute(sql)  # would be a syntax error with bare quoting


def test_optimization_create_index_rejects_an_unknown_index_type():
    payload = {"table": "t", "columns": ["a"], "type": "btree (a); DROP TABLE t; --"}
    assert OptimizationAgent._build_create_index(payload) is None


def test_optimization_create_index_rejects_non_list_columns():
    assert OptimizationAgent._build_create_index({"table": "t", "columns": "a; DROP TABLE t", "type": "btree"}) is None


def test_optimization_create_index_default_type_still_works():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (a INTEGER)")
    sql = OptimizationAgent._build_create_index({"table": "t", "columns": ["a"]})
    assert sql is not None and "USING btree" in sql
