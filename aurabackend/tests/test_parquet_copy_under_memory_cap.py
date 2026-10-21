"""BUG-490: the catalog's Parquet copy must fit under the per-connection memory cap
and must not leave a partial file behind when it cannot."""
from __future__ import annotations

from types import SimpleNamespace

import duckdb

import shared.data_utils as du


def _obj(tmp_path):
    src = tmp_path / "big.csv"
    src.write_text("a\n1\n")
    return SimpleNamespace(name="big.csv", fingerprint="1|1", duckdb_uri=src.as_posix())


class _Recorder:
    def __init__(self, con):
        self.con, self.sql = con, []

    def execute(self, sql, *a):
        self.sql.append(sql)
        return self.con.execute(sql, *a)


def test_the_copy_is_written_single_threaded_and_threads_are_restored(tmp_path):
    con = duckdb.connect()
    con.execute("SET threads=4")
    con.execute("CREATE TABLE t AS SELECT range AS i FROM range(1000)")
    rec = _Recorder(con)
    loader = du._parquet_loader(rec, _obj(tmp_path), "t")
    assert loader and loader["read_fn"] == "read_parquet"
    copy_at = next(i for i, s in enumerate(rec.sql) if s.startswith("COPY"))
    assert "SET threads=1" in rec.sql[:copy_at]
    assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 4
    assert duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{loader['duckdb_uri']}')").fetchone()[0] == 1000


def test_a_failed_copy_leaves_no_partial_file(tmp_path):
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT range AS i, md5(range::varchar) AS s FROM range(200000)")
    con.execute("SET memory_limit='1MB'")  # forces the COPY to fail
    assert du._parquet_loader(con, _obj(tmp_path), "t") is None
    left = list((tmp_path / du._CATALOG_DIRNAME).iterdir())
    assert not [f for f in left if f.name.endswith(".tmp") or f.name.endswith(".parquet")], left
