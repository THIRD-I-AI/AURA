"""
AURA Data Utils Tests
======================
Tests for header detection, column name inference, relationship detection,
context formatting, and serialization helpers.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.data_utils import (
    _columns_might_relate,
    _format_context_for_llm,
    _header_cache_path,
    _infer_column_name,
    _looks_like_header,
    _serialize,
    detect_relationships,
    infer_headers_with_llm,
    smart_load_csv,
)

# ── _looks_like_header ──────────────────────────────────────────────

def test_looks_like_header_true():
    first = ("name", "age", "email")
    second = ("Alice", "30", "alice@example.com")
    assert _looks_like_header(first, second) is True


def test_looks_like_header_false_numeric_first_row():
    first = ("100", "200.5", "300")
    second = ("Alice", "30", "alice@example.com")
    assert _looks_like_header(first, second) is False


def test_looks_like_header_empty_rows():
    assert _looks_like_header((), ()) is True


def test_looks_like_header_email_in_first_row():
    first = ("alice@example.com", "bob@example.com")
    second = ("data1", "data2")
    assert _looks_like_header(first, second) is False


def test_looks_like_header_long_strings():
    first = ("This is a very long string that is definitely not a header name at all",)
    second = ("data",)
    assert _looks_like_header(first, second) is False


# ── _infer_column_name ──────────────────────────────────────────────

def test_infer_column_name_empty():
    assert _infer_column_name([], 0) == "column_0"


def test_infer_column_name_empty_values():
    assert _infer_column_name([None, "", None], 3) == "column_3"


def test_infer_column_name_emails():
    vals = ["alice@example.com", "bob@test.org", "carol@mail.com"]
    assert _infer_column_name(vals, 0) == "email"


def test_infer_column_name_phones():
    vals = ["555-1234", "(555) 867-5309", "+1 555 000 1234"]
    assert _infer_column_name(vals, 0) == "phone"


def test_infer_column_name_dates():
    # Dates with slashes bypass the phone regex (which matches digits+dashes)
    vals = ["2024/01/15", "2024/02/20", "2024/03/25"]
    assert _infer_column_name(vals, 0) == "date"


def test_infer_column_name_dates_non_zero_col():
    vals = ["2024/01/15", "2024/02/20", "2024/03/25"]
    assert _infer_column_name(vals, 3) == "order_date"


def test_infer_column_name_timestamps():
    # Timestamps with slashes and longer than 20 chars bypass the phone regex
    vals = ["2024/01/15 10:30:00", "2024/02/20 14:45:00"]
    assert _infer_column_name(vals, 0) == "date"


def test_infer_column_name_timestamps_non_zero_col():
    vals = ["2024-01-15 10:30:00", "2024-02-20 14:45:00"]
    assert _infer_column_name(vals, 2) == "order_date"


def test_infer_column_name_sequential_ints_col0():
    vals = ["1", "2", "3", "4", "5"]
    assert _infer_column_name(vals, 0) == "id"


def test_infer_column_name_small_ints():
    vals = ["10", "20", "30", "50"]
    assert _infer_column_name(vals, 2) == "value_2"


def test_infer_column_name_large_ints():
    vals = ["10001", "10002", "10003"]
    assert _infer_column_name(vals, 1) == "id_1"


def test_infer_column_name_floats():
    vals = ["19.99", "29.99", "39.99"]
    assert _infer_column_name(vals, 4) == "amount_4"


def test_infer_column_name_capitalized_names_col1():
    vals = ["Alice", "Bob", "Carol"]
    assert _infer_column_name(vals, 1) == "first_name"


def test_infer_column_name_capitalized_names_col2():
    vals = ["Smith", "Jones", "Brown"]
    assert _infer_column_name(vals, 2) == "last_name"


def test_infer_column_name_capitalized_names_other():
    vals = ["Alpha", "Beta", "Gamma"]
    assert _infer_column_name(vals, 5) == "name_5"


def test_infer_column_name_generic_fallback():
    vals = ["abc123!@#", "def456!@#", "ghi789!@#"]
    assert _infer_column_name(vals, 7) == "column_7"


# ── _columns_might_relate ───────────────────────────────────────────

def test_columns_might_relate_exact_id_match():
    assert _columns_might_relate("customer_id", "customer_id", "INTEGER", "INTEGER") is True


def test_columns_might_relate_exact_key_match():
    assert _columns_might_relate("order_key", "order_key", "VARCHAR", "VARCHAR") is True


def test_columns_might_relate_exact_code_match():
    assert _columns_might_relate("status_code", "status_code", "VARCHAR", "VARCHAR") is True


def test_columns_might_relate_fk_to_id():
    assert _columns_might_relate("customer_id", "id", "INTEGER", "INTEGER") is True


def test_columns_might_relate_id_to_fk():
    assert _columns_might_relate("id", "customer_id", "INTEGER", "INTEGER") is True


def test_columns_might_relate_shared_id_prefix():
    assert _columns_might_relate("customer_id", "cust_id", "INTEGER", "INTEGER") is True


def test_columns_might_relate_same_name_both_int():
    assert _columns_might_relate("quantity", "quantity", "INTEGER", "BIGINT") is True


def test_columns_might_relate_unrelated():
    assert _columns_might_relate("name", "email", "VARCHAR", "VARCHAR") is False


# ── _serialize ──────────────────────────────────────────────────────

def test_serialize_none():
    assert _serialize(None) is None


def test_serialize_int():
    assert _serialize(42) == 42


def test_serialize_float():
    assert _serialize(3.14) == 3.14


def test_serialize_bool():
    assert _serialize(True) is True


def test_serialize_str():
    assert _serialize("hello") == "hello"


def test_serialize_other():
    from datetime import datetime
    dt = datetime(2024, 1, 1)
    assert isinstance(_serialize(dt), str)


# ── infer_headers_with_llm ──────────────────────────────────────────

def test_infer_headers_with_llm_no_llm_available():
    with patch("shared.data_utils.infer_headers_with_llm") as mock_fn:
        mock_fn.return_value = None
        result = mock_fn("test.csv", ["INTEGER", "VARCHAR"], [(1, "Alice")])
        assert result is None


def test_infer_headers_with_llm_import_error():
    """When llm_provider cannot be imported, returns None."""
    with patch.dict("sys.modules", {"shared.llm_provider": None}):
        result = infer_headers_with_llm("test.csv", ["INTEGER"], [(1,)])
        assert result is None


# ── detect_relationships ────────────────────────────────────────────

def test_detect_relationships_matching_ids():
    mock_conn = MagicMock()
    # Overlap query returns 5 matching values
    mock_conn.execute.return_value.fetchone.return_value = (5,)

    tables = {
        "customers": {
            "columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR"}],
        },
        "orders": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}, {"name": "total", "type": "FLOAT"}],
        },
    }

    # Mock the count distinct calls: customers.id has 10 unique, orders.customer_id has 5
    call_count = {"n": 0}


    def side_effect(sql):
        result = MagicMock()
        if "INTERSECT" in sql:
            result.fetchone.return_value = (5,)
        elif "COUNT(DISTINCT" in sql:
            call_count["n"] += 1
            if call_count["n"] == 1:
                result.fetchone.return_value = (10,)
            else:
                result.fetchone.return_value = (5,)
        return result

    mock_conn.execute = MagicMock(side_effect=side_effect)
    rels = detect_relationships(mock_conn, tables)
    assert len(rels) >= 1
    assert rels[0]["type"] == "foreign_key"


def test_detect_relationships_no_overlap():
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (0,)

    tables = {
        "t1": {"columns": [{"name": "id", "type": "INTEGER"}]},
        "t2": {"columns": [{"name": "id", "type": "INTEGER"}]},
    }
    rels = detect_relationships(mock_conn, tables)
    assert len(rels) == 0


def test_detect_relationships_query_exception():
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("db error")

    tables = {
        "t1": {"columns": [{"name": "id", "type": "INTEGER"}]},
        "t2": {"columns": [{"name": "id", "type": "INTEGER"}]},
    }
    rels = detect_relationships(mock_conn, tables)
    assert rels == []


# ── _format_context_for_llm ─────────────────────────────────────────

def test_format_context_basic():
    tables = {
        "users": {
            "columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR"}],
            "row_count": 100,
            "sample_data": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            "headers_inferred": False,
        },
    }
    result = _format_context_for_llm(tables, [])
    assert "users" in result
    assert "100 rows" in result
    assert "id (INTEGER)" in result


def test_format_context_with_relationships():
    tables = {
        "orders": {
            "columns": [{"name": "id", "type": "INTEGER"}],
            "row_count": 50,
            "sample_data": [],
            "headers_inferred": False,
        },
    }
    rels = [{
        "from_table": "orders",
        "from_column": "customer_id",
        "to_table": "customers",
        "to_column": "id",
        "overlap_count": 10,
    }]
    result = _format_context_for_llm(tables, rels)
    assert "Relationships" in result
    assert "JOIN" in result


def test_format_context_headers_inferred_note():
    tables = {
        "data": {
            "columns": [{"name": "col1", "type": "INTEGER"}],
            "row_count": 10,
            "sample_data": [],
            "headers_inferred": True,
        },
    }
    result = _format_context_for_llm(tables, [])
    assert "inferred" in result.lower()


# ── sidecar header cache ────────────────────────────────────────────
# Inferred headers are persisted next to the data file so the (slow, rate-
# limited) LLM inference runs exactly once per file version — surviving
# process restarts and the in-memory schema_cache's TTL expiry.

duckdb = pytest.importorskip("duckdb")


def _write_headerless_csv(path) -> None:
    # Numeric-leading rows so DuckDB's auto-detector assigns generic
    # column0/column1/... names, triggering the inference path.
    path.write_text(
        "1,Orlando,Gee\n2,Keith,Harris\n3,Donna,Carreras\n4,Janet,Gates\n",
        encoding="utf-8",
    )


def test_sidecar_cache_skips_second_inference(tmp_path):
    csv = tmp_path / "people.csv"
    _write_headerless_csv(csv)

    calls = {"n": 0}

    def fake_infer(file_name, col_types, sample_rows):
        calls["n"] += 1
        return [f"field_{i}" for i in range(len(col_types))]

    with patch("shared.data_utils.infer_headers_with_llm", side_effect=fake_infer):
        first = smart_load_csv(duckdb.connect(":memory:"), str(csv), "t1")
        assert calls["n"] == 1
        assert first["headers_inferred"] is True
        assert _header_cache_path(str(csv)).exists()

        # Fresh connection → must read the sidecar, NOT re-infer.
        second = smart_load_csv(duckdb.connect(":memory:"), str(csv), "t2")
        assert calls["n"] == 1, "second load must not call the LLM again"

    cols_first = [c["name"] for c in first["columns"]]
    cols_second = [c["name"] for c in second["columns"]]
    assert cols_first == cols_second == ["field_0", "field_1", "field_2"]


def test_sidecar_cache_invalidated_on_file_change(tmp_path):
    csv = tmp_path / "people.csv"
    _write_headerless_csv(csv)

    calls = {"n": 0}

    def fake_infer(file_name, col_types, sample_rows):
        calls["n"] += 1
        return [f"field_{i}" for i in range(len(col_types))]

    with patch("shared.data_utils.infer_headers_with_llm", side_effect=fake_infer):
        smart_load_csv(duckdb.connect(":memory:"), str(csv), "t1")
        assert calls["n"] == 1

        # Change the file: different mtime AND size + an extra column → the
        # cached (mtime, size, n_cols) fingerprint no longer matches.
        csv.write_text(
            "1,Orlando,Gee,extra\n2,Keith,Harris,more\n3,Donna,Carreras,vals\n",
            encoding="utf-8",
        )
        smart_load_csv(duckdb.connect(":memory:"), str(csv), "t2")
        assert calls["n"] == 2, "changed file must force re-inference"


def test_sidecar_cache_not_seen_as_data_file(tmp_path):
    """The sidecar dir must not be mistaken for a data file by the upload
    scan — it lives in a dot-prefixed subdir with no data extension."""
    csv = tmp_path / "people.csv"
    _write_headerless_csv(csv)
    with patch(
        "shared.data_utils.infer_headers_with_llm",
        side_effect=lambda *a: ["a", "b", "c"],
    ):
        smart_load_csv(duckdb.connect(":memory:"), str(csv), "t1")

    sidecar = _header_cache_path(str(csv))
    assert sidecar.exists()
    assert sidecar.parent.name == ".aura_header_cache"
    assert sidecar.parent.suffix == ""  # dir is skipped by the extension filter
