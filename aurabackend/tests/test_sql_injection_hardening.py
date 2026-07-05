"""Proves the upload→index→chat data path cannot be SQL-injected through
attacker-controlled identifiers (CSV headers, inferred column names, file
stems, table names).

The threat: DuckDB's ``execute()`` runs multiple ``;``-separated statements,
and uploaded-file headers/stems are interpolated into DDL. A header like
``x" ; CREATE TABLE pwned AS SELECT 1; --`` spliced raw would break out of the
identifier and run a second statement. ``quote_identifier``/``quote_literal``
make that impossible; these tests load genuinely-hostile inputs through the
real loaders and assert no side-effect statement ever runs."""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.data_utils import detect_relationships, smart_load_csv
from shared.sql_identifiers import quote_identifier, quote_literal

duckdb = pytest.importorskip("duckdb")

# A payload that, interpolated raw into `... "{name}" ...`, closes the
# identifier and runs a second statement creating a sentinel table.
INJECTION = 'x" ; CREATE TABLE pwned AS SELECT 1 AS hacked; --'


def _table_names(con) -> set[str]:
    return {r[0] for r in con.execute("SHOW TABLES").fetchall()}


# ── unit: the quoting helpers ───────────────────────────────────────────────

def test_quote_identifier_doubles_embedded_quote():
    assert quote_identifier('a"b') == '"a""b"'
    # the injection payload becomes ONE inert identifier
    assert quote_identifier(INJECTION) == '"x"" ; CREATE TABLE pwned AS SELECT 1 AS hacked; --"'


def test_quote_literal_doubles_embedded_quote():
    assert quote_literal("a'b") == "'a''b'"
    assert quote_literal("x'); ATTACH 'evil") == "'x''); ATTACH ''evil'"


def test_quoting_helpers_reject_nul_byte():
    with pytest.raises(ValueError):
        quote_identifier("a\x00b")
    with pytest.raises(ValueError):
        quote_literal("a\x00b")


# ── integration: hostile inputs through the real loaders ────────────────────

def test_malicious_inferred_header_cannot_inject(tmp_path):
    """Headerless CSV → the (mocked) header inference returns a malicious name
    that flows into ALTER TABLE ... RENAME COLUMN. It must land as a literal
    column name, not execute a second statement."""
    csv = tmp_path / "data.csv"
    csv.write_text("1,Orlando\n2,Keith\n3,Donna\n", encoding="utf-8")

    con = duckdb.connect(":memory:")
    with patch(
        "shared.data_utils.infer_headers_with_llm",
        side_effect=lambda *a: [INJECTION, "name"],
    ):
        result = smart_load_csv(con, str(csv), "loaded", use_llm=True)

    assert "pwned" not in _table_names(con), "injection ran a second statement!"
    # the payload survived verbatim as a (harmless) column name → proves it was
    # treated as data, not SQL
    assert INJECTION in [c["name"] for c in result["columns"]]


def test_malicious_table_name_cannot_inject(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("amount,city\n10,NYC\n20,LA\n", encoding="utf-8")

    con = duckdb.connect(":memory:")
    smart_load_csv(con, str(csv), INJECTION, use_llm=False)

    assert "pwned" not in _table_names(con)
    # the data really loaded under the hostile (quoted) table name
    assert INJECTION in _table_names(con)


def test_malicious_header_through_detect_relationships(tmp_path):
    """A real CSV header containing the payload becomes a DuckDB column; when
    that column name is interpolated into detect_relationships' overlap probe,
    it must not inject."""
    con = duckdb.connect(":memory:")
    # two tables sharing a hostile-named, overlapping key column
    con.execute(f'CREATE TABLE a ({quote_identifier(INJECTION)} INTEGER, v INTEGER)')
    con.execute(f'CREATE TABLE b ({quote_identifier(INJECTION)} INTEGER, w INTEGER)')
    con.execute('INSERT INTO a VALUES (1, 100), (2, 200)')
    con.execute('INSERT INTO b VALUES (1, 9), (2, 9)')

    tables = {
        "a": {"columns": [{"name": INJECTION, "type": "INTEGER"}, {"name": "v", "type": "INTEGER"}]},
        "b": {"columns": [{"name": INJECTION, "type": "INTEGER"}, {"name": "w", "type": "INTEGER"}]},
    }
    detect_relationships(con, tables)

    assert "pwned" not in _table_names(con)
