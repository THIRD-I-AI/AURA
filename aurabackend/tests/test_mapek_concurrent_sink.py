"""Regression tests for concurrent multi-pipeline persistence to a shared sink.

Bottleneck #4 (enterprise concurrency): when several MAPE-K workers heal
different pipelines but share one DuckDB file, ``_write_duckdb_atomic`` used to
run ``CREATE TABLE IF NOT EXISTS`` on *every* batch. DuckDB's optimistic
concurrency control aborts concurrent catalog mutations with a write-write
conflict, so a losing worker's transaction rolled back and silently dropped the
batch (~3% loss at 4-16 concurrent writers). Concurrent INSERTs into an
existing table do not conflict.

The fix creates the table at most once per process (``_table_ready``), then
INSERTs only, retrying with a small jittered backoff if the first-batch CREATE
races another worker. These tests exercise the real ``_write_parquet`` +
``_write_duckdb_atomic`` path through a thread pool sharing one DuckDB file and
assert zero batch loss and no schema corruption.
"""
from __future__ import annotations

import threading

import pytest

from uasr.mapek_worker import MAPEKConfig, MAPEKWorker
from uasr.models import BatchPayload

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")


def _make_worker(source_id, duckdb_path, parquet_dir):
    cfg = MAPEKConfig(
        source_id=source_id,
        duckdb_path=str(duckdb_path),
        parquet_dir=str(parquet_dir),
        table_name="uasr_events",
    )
    w = MAPEKWorker(config=cfg)
    w._duckdb_con = w._open_duckdb()
    return w


def _batch(source_id, seq, n_rows=50):
    rows = [{"source_id": source_id, "batch_id": f"{seq}", "v": float(i), "x": i * 1.5}
            for i in range(n_rows)]
    return BatchPayload(source_id=source_id, batch_id=f"{source_id}-{seq}",
                        columns=list(rows[0].keys()), rows=rows)


def _persist_many(worker, source_id, n_batches, rows_each, errors):
    for seq in range(n_batches):
        try:
            path = worker._write_parquet(_batch(source_id, seq, rows_each))
            worker._write_duckdb_atomic(path)
        except Exception as exc:  # pragma: no cover — the bug being guarded
            errors.append((source_id, seq, type(exc).__name__))


@pytest.mark.parametrize("n_workers", [4, 8, 16])
def test_concurrent_shared_sink_no_batch_loss(tmp_path, n_workers):
    """N workers sharing one DuckDB file commit every batch, no loss."""
    shared_db = tmp_path / "shared_lake.duckdb"
    pq_dir = tmp_path / "pq"
    n_batches, rows_each = 15, 40

    workers = [_make_worker(f"src{w}", shared_db, pq_dir) for w in range(n_workers)]
    errors: list = []
    threads = [
        threading.Thread(target=_persist_many,
                         args=(workers[w], f"src{w}", n_batches, rows_each, errors))
        for w in range(n_workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for w in workers:
        w._duckdb_con.close()

    assert errors == [], f"persist raised under concurrency: {errors[:5]}"

    con = duckdb.connect(str(shared_db))
    total = con.execute('SELECT COUNT(*) FROM "uasr_events"').fetchone()[0]
    per_source = dict(con.execute(
        'SELECT source_id, COUNT(*) FROM "uasr_events" GROUP BY source_id'
    ).fetchall())
    con.close()

    expected = n_workers * n_batches * rows_each
    assert total == expected, f"row loss: got {total}, expected {expected}"
    for w in range(n_workers):
        assert per_source[f"src{w}"] == n_batches * rows_each


def test_create_once_then_insert_only(tmp_path):
    """After the first successful persist, _table_ready stays True so CREATE
    is not re-issued (the property that closes the catalog-race window)."""
    db = tmp_path / "lake.duckdb"
    w = _make_worker("s0", db, tmp_path / "pq")
    assert w._table_ready is False
    w._write_duckdb_atomic(w._write_parquet(_batch("s0", 0)))
    assert w._table_ready is True
    # subsequent writes keep committing with the flag already set
    w._write_duckdb_atomic(w._write_parquet(_batch("s0", 1)))
    con = w._duckdb_con
    assert con.execute('SELECT COUNT(*) FROM "uasr_events"').fetchone()[0] == 100
    con.close()


def test_single_writer_unchanged(tmp_path):
    """Single-writer behaviour is byte-for-byte the same: table created,
    rows inserted, schema intact."""
    db = tmp_path / "lake.duckdb"
    w = _make_worker("s0", db, tmp_path / "pq")
    for seq in range(5):
        w._write_duckdb_atomic(w._write_parquet(_batch("s0", seq, 30)))
    con = w._duckdb_con
    total = con.execute('SELECT COUNT(*) FROM "uasr_events"').fetchone()[0]
    cols = [r[0] for r in con.execute('DESCRIBE "uasr_events"').fetchall()]
    con.close()
    assert total == 150
    assert set(cols) == {"source_id", "batch_id", "v", "x"}
