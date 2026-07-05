"""Subsystem B (slice) — AI-native Data Card: a compact per-table semantic
summary (type, cardinality, null fraction, sample values) the commander reads
on demand to *reason* about the data, not just its column names. E.g. a column
with distinct=2 and samples ['approved','denied'] is a binary outcome; high
cardinality + id-like samples is a key. Cheap DuckDB queries; injection-safe."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.commander_tools import build_default_registry

duckdb = pytest.importorskip("duckdb")


def _con():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE loans (id INTEGER, race VARCHAR, approved BOOLEAN, amount INTEGER)")
    con.execute("INSERT INTO loans VALUES "
                "(1,'A',true,100),(2,'B',false,200),(3,'A',true,NULL),(4,'B',true,150)")
    return con


def _tables(con):
    return {r[0] for r in con.execute("SHOW TABLES").fetchall()}


def test_data_card_summarizes_table_semantics():
    reg = build_default_registry()
    out = reg.execute("get_data_card", {"table": "loans"}, tenant="t1", con=_con())
    assert out.ok is True
    card = out.value
    assert card["table"] == "loans"
    assert card["row_count"] == 4
    cols = {c["name"]: c for c in card["columns"]}
    assert cols["race"]["distinct"] == 2
    assert set(cols["race"]["samples"]) == {"A", "B"}
    assert cols["approved"]["distinct"] == 2
    assert cols["amount"]["null_frac"] == 0.25      # 1 of 4 null


def test_data_card_unknown_table_is_error():
    reg = build_default_registry()
    out = reg.execute("get_data_card", {"table": "no_such"}, tenant="t1", con=_con())
    assert out.ok is False


def test_data_card_injection_table_name_is_safe():
    reg = build_default_registry()
    con = _con()
    reg.execute("get_data_card",
                {"table": 'loans"; CREATE TABLE pwned AS SELECT 1; --'}, tenant="t1", con=con)
    assert "pwned" not in _tables(con)


def test_data_card_in_specs():
    assert "get_data_card" in {s["name"] for s in build_default_registry().specs()}
