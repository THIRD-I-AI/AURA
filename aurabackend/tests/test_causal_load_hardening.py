"""BUG-197: causal /discover loaded data with a request-chosen DuckDB path and a raw WHERE clause."""
from __future__ import annotations

import duckdb
import pytest
from fastapi import HTTPException

from causal_service.main import _load
from causal_service.models import DataSource


@pytest.fixture()
def lake(tmp_path, monkeypatch):
    db = tmp_path / "lake" / "uasr_lake.duckdb"
    db.parent.mkdir()
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE metrics (a INTEGER, b INTEGER)")
    con.execute("INSERT INTO metrics VALUES (1, 10), (2, 20), (3, 30)")
    con.close()
    monkeypatch.setenv("UASR_DUCKDB_PATH", str(db))
    return db


def test_normal_where_and_limit_still_work(lake):
    df = _load(DataSource(duckdb_table="metrics", where="a >= 2", limit=10), "training_data")
    assert sorted(df["a"].tolist()) == [2, 3]


@pytest.mark.parametrize("where", [
    "1=1; COPY (SELECT 1) TO 'x.csv'",
    "a IN (SELECT 1 FROM read_csv('/etc/passwd'))",
    "a IN (SELECT length(content) FROM read_text('secret.txt'))",
    "1=1 UNION SELECT * FROM 'file.csv'",
])
def test_a_where_that_reaches_files_is_refused(lake, where):
    with pytest.raises(HTTPException) as exc:
        _load(DataSource(duckdb_table="metrics", where=where), "training_data")
    assert exc.value.status_code == 400


def test_the_connection_is_locked_even_if_the_guard_misses_something(lake, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    # A syntax the blocklist may not know: the locked connection still refuses the file read.
    where = f"a IN (SELECT 1 FROM (SELECT * FROM glob('{secret.as_posix()}')))"
    try:
        _load(DataSource(duckdb_table="metrics", where=where), "training_data")
    except HTTPException:
        return  # refused by the guard -- fine
    except duckdb.Error:
        return  # refused by the locked connection -- fine
    pytest.fail("a file-system function ran on the causal connection")


def test_a_database_path_outside_the_lake_directory_is_refused(lake, tmp_path):
    other = tmp_path / "other.duckdb"
    duckdb.connect(str(other)).close()
    with pytest.raises(HTTPException) as exc:
        _load(DataSource(duckdb_table="metrics", duckdb_path=str(other)), "training_data")
    assert exc.value.status_code == 400
    assert "data directory" in exc.value.detail


def test_a_table_name_with_a_quote_is_quoted_not_spliced(lake):
    con = duckdb.connect(str(lake))
    con.execute('CREATE TABLE "we""ird" (a INTEGER)')
    con.execute('INSERT INTO "we""ird" VALUES (7)')
    con.close()
    df = _load(DataSource(duckdb_table='we"ird'), "training_data")
    assert df["a"].tolist() == [7]
