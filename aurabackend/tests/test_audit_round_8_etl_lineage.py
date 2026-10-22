"""Audit round 8, etl-lineage area: BUG-497, BUG-498, BUG-500.

The ETL tests execute the generated SQL against a real DuckDB connection; a
string-shape check would not show that DuckDB binds a bare ``nan`` to a column.
"""
from __future__ import annotations

import math
import os
import sys

import duckdb
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.persistence import _extract_tables  # noqa: E402
from api_gateway.routers.etl import ETLTransformStep, _build_transform_sql  # noqa: E402
from pipeline.engine import PipelineEngine  # noqa: E402
from pipeline.models import ProcessingStep, StepType  # noqa: E402

# ── BUG-497: NaN / inf fill values ──────────────────────────────────


@pytest.fixture()
def con():
    conn = duckdb.connect(":memory:")
    # A column literally named "inf" is what turned the bug from an error into
    # silently wrong data: the bare word bound to it.
    conn.execute('CREATE TABLE src (status VARCHAR, score DOUBLE, "inf" DOUBLE)')
    conn.execute("INSERT INTO src VALUES (NULL, NULL, 7.0), ('ok', 1.5, 8.0)")
    yield conn
    conn.close()


def _etl_rows(con, steps):
    sql = _build_transform_sql("src", steps, con=con)
    return con.execute(f"SELECT status, score FROM ({sql}) ORDER BY score NULLS FIRST").fetchall()


def test_bug497_single_column_nan_fill_in_text_column(con):
    rows = _etl_rows(con, [ETLTransformStep(id="s1", type="fill_missing",
                                            config={"column": "status", "value": "NaN"})])
    assert rows[0][0] == "NaN"


def test_bug497_single_column_inf_fill_does_not_bind_to_inf_column(con):
    rows = _etl_rows(con, [ETLTransformStep(id="s1", type="fill_missing",
                                            config={"column": "score", "value": "inf"})])
    filled = [r[1] for r in rows]
    assert 7.0 not in filled, "fill bound to the column named inf"
    assert math.inf in filled


def test_bug497_star_fill_inf_does_not_bind_to_inf_column(con):
    rows = _etl_rows(con, [ETLTransformStep(id="s1", type="fill_missing",
                                            config={"column": "*", "value": "inf"})])
    filled = [r[1] for r in rows]
    assert 7.0 not in filled
    assert math.inf in filled


def test_bug497_star_fill_nan_into_integer_column(con):
    con.execute("CREATE TABLE ints (n INTEGER)")
    con.execute("INSERT INTO ints VALUES (NULL), (3)")
    sql = _build_transform_sql("ints", [ETLTransformStep(
        id="s1", type="fill_missing", config={"column": "*", "value": "NaN"})], con=con)
    vals = [r[0] for r in con.execute(f"SELECT n FROM ({sql})").fetchall()]
    assert any(isinstance(v, float) and math.isnan(v) for v in vals)
    assert 3 in vals


def test_bug497_finite_values_still_render_as_numbers(con):
    rows = _etl_rows(con, [ETLTransformStep(id="s1", type="fill_missing",
                                            config={"column": "score", "value": "2.5"})])
    assert sorted(r[1] for r in rows) == [1.5, 2.5]


def test_bug497_pipeline_engine_star_fill_inf(con):
    sql = PipelineEngine()._step_to_sql(
        con, ProcessingStep(id="s1", type=StepType.FILL_MISSING,
                            config={"column": "*", "fill_value": "inf"}), "src")
    filled = [r[0] for r in con.execute(f"SELECT score FROM ({sql})").fetchall()]
    assert 7.0 not in filled
    assert math.inf in filled


# ── BUG-498: lineage table extraction ───────────────────────────────


def test_bug498_cte_alias_is_not_a_table():
    assert _extract_tables(
        "WITH recent AS (SELECT * FROM orders WHERE d > 1) SELECT * FROM recent"
    ) == {"orders"}


def test_bug498_schema_qualified_tables_stay_distinct():
    assert _extract_tables(
        "SELECT * FROM a.orders JOIN b.orders USING (id)"
    ) == {"a.orders", "b.orders"}


def test_bug498_table_function_yields_no_empty_name():
    assert "" not in _extract_tables("SELECT * FROM read_csv_auto('x.csv')")


def test_bug498_qualified_table_sharing_a_cte_name_is_kept():
    assert _extract_tables(
        "WITH orders AS (SELECT 1) SELECT * FROM orders JOIN sales.orders USING (id)"
    ) == {"sales.orders"}


# ── BUG-500: source file named _etl_output.<ext> ────────────────────


def test_bug500_source_named_etl_output_executes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    from shared.storage import get_storage_backend, reset_storage_backend

    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    reset_storage_backend()
    get_storage_backend().write("default", "_etl_output.csv", b"region,revenue\nN,100\nS,200\n")

    # No `with`: driving the lifespan leaves aiosqlite threads that hang pytest.
    client = TestClient(app)
    r = client.post("/api/v1/etl/execute", json={
        "source_file": "_etl_output.csv", "transforms": [], "preview_only": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert len(body["preview"]) == 2
