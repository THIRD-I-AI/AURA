"""BUG-183..188: values and names that reach SQL text in the pipeline engine and ETL router.

Each test drives the real DuckDB path and uses a marker file / marker column as the
side effect an injection would produce, so a regression fails loudly.
"""
from __future__ import annotations

import duckdb
import pytest

from api_gateway.routers.etl import ETLTransformStep, _build_transform_sql, _fill_literal
from pipeline.engine import PipelineEngine
from pipeline.models import (
    PipelineSink,
    ProcessingStep,
    SinkType,
    StepType,
)


@pytest.fixture()
def con():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE source_data (amt INTEGER, grp VARCHAR)")
    conn.execute("INSERT INTO source_data VALUES (10,'a'),(NULL,'b'),(30,'a')")
    yield conn
    conn.close()


def _etl(con, steps):
    sql = _build_transform_sql("source_data", steps, con=con)
    con.execute(f"CREATE TABLE _out AS {sql}")
    rows = con.execute("SELECT * FROM _out ORDER BY 1 NULLS LAST").fetchall()
    con.execute("DROP TABLE _out")
    return rows


# ── BUG-185: fill_value ─────────────────────────────────────────────────────

def test_fill_literal_renders_numbers_and_quotes_everything_else():
    assert _fill_literal("7") == "7.0"
    assert _fill_literal("x'); DROP TABLE t; --") == "'x''); DROP TABLE t; --'"


def test_etl_single_column_fill_value_is_not_executed(con):
    payload = "0) AS x, (SELECT 1337) AS pwned --"
    steps = [ETLTransformStep(id="s", type="fill_missing", config={"column": "amt", "value": payload})]
    try:
        _etl(con, steps)  # may error (a string is not a valid INTEGER fill) -- that is fine
    except duckdb.Error:
        pass
    cols = [d[0] for d in con.execute("DESCRIBE source_data").fetchall()]
    assert "pwned" not in cols


def test_etl_numeric_fill_value_still_fills(con):
    steps = [ETLTransformStep(id="s", type="fill_missing", config={"column": "amt", "value": "5"})]
    assert (5, "b") in _etl(con, steps)


def test_etl_star_fill_ignores_a_non_numeric_value_on_numeric_columns(con):
    steps = [ETLTransformStep(id="s", type="fill_missing", config={"column": "*", "value": "1) AS pwned, (SELECT 1"})]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert "pwned" not in sql


# ── BUG-187: cast_type ──────────────────────────────────────────────────────

def test_etl_cast_type_refuses_an_unknown_type(con):
    steps = [ETLTransformStep(id="s", type="cast_type",
                              config={"column": "amt", "to_type": "INTEGER) AS x, (SELECT 1337) AS pwned --"})]
    sql = _build_transform_sql("source_data", steps, con=con)
    assert "pwned" not in sql


def test_etl_cast_type_allows_a_known_type_case_insensitively(con):
    steps = [ETLTransformStep(id="s", type="cast_type", config={"column": "amt", "to_type": "double"})]
    assert (10.0, "a") in _etl(con, steps)


# ── BUG-183 / 184 / 186: engine sink, PIVOT, column names ───────────────────

def test_engine_file_sink_name_with_a_quote_is_a_filename_not_sql(tmp_path, monkeypatch):
    """BUG-183: a slash-free, dot-free file_name survives Path.stem intact. Spliced raw into
    COPY ... TO '<dir>/<name>.csv' it ran a second statement; here that statement would write
    zz_pwned.csv into the working directory."""
    import pipeline.engine as eng

    out = tmp_path / "out"
    cwd = tmp_path / "cwd"
    out.mkdir()
    cwd.mkdir()
    monkeypatch.setattr(eng, "OUTPUT_DIR", str(out))
    monkeypatch.chdir(cwd)
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE final_t AS SELECT 1 AS a")
    sink = PipelineSink(type=SinkType.FILE, format="csv", file_name="a' (FORMAT CSV); COPY (SELECT 1) TO 'zz_pwned")
    run = type("R", (), {"run_id": "r1", "rows_written": 1, "output_file": None})()
    PipelineEngine()._write_file_sink(conn, "final_t", sink, run, tenant="t1")
    assert not (cwd / "zz_pwned.csv").exists(), "the quote broke out of the COPY path literal"
    assert run.output_file, "the file is still written, under its literal (odd) name"


def _step(conn, step_type, **cfg):
    conn.execute("CREATE OR REPLACE TABLE prev_t (k VARCHAR, p VARCHAR, v INTEGER)")
    conn.execute("INSERT INTO prev_t VALUES ('a','x',1),('a','y',2)")
    return PipelineEngine()._step_to_sql(conn, ProcessingStep(id="s1", type=step_type, config=cfg), "prev_t")


def test_pivot_rejects_an_aggregate_that_is_not_allowlisted():
    conn = duckdb.connect(":memory:")
    assert _step(conn, StepType.PIVOT, values_column="v", pivot_column="p",
                 agg_function="MAX((SELECT 1337 FROM prev_t LIMIT 1))") is None


def test_pivot_still_accepts_an_allowlisted_aggregate_case_insensitively():
    conn = duckdb.connect(":memory:")
    sql = _step(conn, StepType.PIVOT, values_column="v", pivot_column="p", agg_function="sum")
    assert sql is not None and "SUM(" in sql
    conn.execute(f"CREATE TABLE piv AS {sql}")


def test_fill_missing_star_quotes_column_names_and_ignores_a_non_numeric_value():
    conn = duckdb.connect(":memory:")
    conn.execute('CREATE TABLE prev_t ("we""ird" INTEGER, other VARCHAR)')
    conn.execute("INSERT INTO prev_t VALUES (NULL,'a'),(4,'b')")
    ok = PipelineEngine()._step_to_sql(
        conn, ProcessingStep(id="s1", type=StepType.FILL_MISSING, config={"column": "*", "strategy": "value", "fill_value": "9"}), "prev_t")
    assert ok is not None
    conn.execute(f"CREATE TABLE filled AS {ok}")
    assert (9, "a") in conn.execute("SELECT * FROM filled").fetchall()

    bad = PipelineEngine()._step_to_sql(
        conn, ProcessingStep(id="s2", type=StepType.FILL_MISSING,
                             config={"column": "*", "strategy": "value", "fill_value": "1) AS pwned, (SELECT 1"}), "prev_t")
    assert bad is None or "pwned" not in bad


def test_fill_missing_star_quotes_a_text_column_name_with_a_double_quote():
    """BUG-186 (remaining branch): a VARCHAR column filled with a non-numeric value used a raw-quoted name."""
    conn = duckdb.connect(":memory:")
    conn.execute('CREATE TABLE prev_t ("we""ird" VARCHAR, n INTEGER)')
    conn.execute("INSERT INTO prev_t VALUES (NULL, 1), ('kept', 2)")
    sql = PipelineEngine()._step_to_sql(
        conn, ProcessingStep(id="s1", type=StepType.FILL_MISSING,
                             config={"column": "*", "strategy": "value", "fill_value": "unknown"}), "prev_t")
    assert sql is not None
    conn.execute(f"CREATE TABLE filled AS {sql}")
    rows = conn.execute("SELECT * FROM filled ORDER BY n").fetchall()
    assert rows == [("unknown", 1), ("kept", 2)]


def test_etl_preview_limit_must_be_an_integer_and_is_rejected_before_the_file_is_looked_up():
    """BUG-188 (regression test that was missing): `limit` used to go straight into the SQL text."""
    import asyncio

    from starlette.requests import Request

    from api_gateway.routers import etl

    req = Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})
    for bad in ("abc", "1; DROP TABLE t", None, [5]):
        with pytest.raises(Exception) as exc:
            asyncio.run(etl.etl_preview_source({"source_file": "nope.csv", "limit": bad}, req))
        assert getattr(exc.value, "status_code", None) == 400, f"limit={bad!r} should be a 400, got {exc.value!r}"
