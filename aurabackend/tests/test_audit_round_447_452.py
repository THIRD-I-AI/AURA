"""Regression tests for BUG-447..452, found auditing this cycle's storage, guard and estimator changes."""
from __future__ import annotations

import os
import time

import duckdb
import pytest

import shared.data_utils as du
from shared.cache import schema_cache


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    from shared.storage.local import LocalBackend

    backend = LocalBackend(str(tmp_path))
    monkeypatch.setattr("shared.storage.get_storage_backend", lambda: backend)
    folder = tmp_path / backend.tenant_dir("acme").name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "orders.csv").write_text("order_id,amount\n1,5.5\n2,7.0\n")
    return folder


@pytest.fixture(autouse=True)
async def _empty_cache():
    await schema_cache.clear_prefix(du.SCHEMA_CACHE_PREFIX)
    yield
    await schema_cache.clear_prefix(du.SCHEMA_CACHE_PREFIX)


# ── BUG-452 ──────────────────────────────────────────────────────────────


def test_a_confounder_acting_through_a_mediator_is_not_an_instrument():
    from counterfactual_service.engine import _adjustment_set
    from counterfactual_service.iv_estimator import instruments_from_dag

    edges = [("x", "t"), ("x", "m"), ("m", "y"), ("z", "t"), ("t", "y")]
    assert instruments_from_dag(edges, "t", "y") == ["z"]
    assert _adjustment_set({"edges": edges}, "t", "y", ["t", "y", "x", "m", "z"]) == ["x"]
    # A path to the outcome that runs only through the treatment keeps it an instrument
    # (both z and w here reach y only via t).
    assert instruments_from_dag([("z", "w"), ("w", "t"), ("z", "t"), ("t", "y")], "t", "y") == ["w", "z"]


# ── BUG-447 ──────────────────────────────────────────────────────────────


def test_the_storage_limit_counts_files_of_every_extension(tmp_path):
    from shared.upload_retention import tenant_dir_bytes

    (tmp_path / "data.csv").write_bytes(b"x" * 100)
    (tmp_path / "blob.bin").write_bytes(b"x" * 1000)
    (tmp_path / "notes").write_bytes(b"x" * 10)
    assert tenant_dir_bytes(tmp_path) == 1110
    assert tenant_dir_bytes(tmp_path, replacing="blob.bin") == 110
    assert tenant_dir_bytes(None) is None


# ── BUG-449 / BUG-448 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_version_of_a_file_gets_its_own_parquet_copy(uploads):
    first = await du.load_catalog("acme", use_llm=False)
    path_v1 = {ld["table_name"]: ld for ld in first["loaders"]}["orders"]["duckdb_uri"]

    target = uploads / "orders.csv"
    target.write_text("order_id,amount\n1,100.0\n")
    os.utime(target, (time.time() + 5, time.time() + 5))
    second = await du.load_catalog("acme", use_llm=False)
    path_v2 = {ld["table_name"]: ld for ld in second["loaders"]}["orders"]["duckdb_uri"]

    assert path_v1 != path_v2
    con = duckdb.connect(":memory:")
    await du.materialize_tables(con, second, {"orders"})
    assert con.execute("SELECT SUM(amount) FROM orders").fetchone()[0] == pytest.approx(100.0)


def test_pruning_spares_recent_files_and_files_live_in_a_fresh_listing(tmp_path):
    from types import SimpleNamespace

    live = {"a.csv": SimpleNamespace(name="a.csv", fingerprint="1|10")}
    keep_recent = tmp_path / "b.csv.json"  # a newer build just wrote this
    keep_recent.write_text("{}")
    stale = tmp_path / "gone.csv.json"
    stale.write_text("{}")
    old = time.time() - 2 * du._PRUNE_MIN_AGE_S
    os.utime(stale, (old, old))

    du._prune_sidecars(tmp_path, live)
    assert keep_recent.exists() and not stale.exists()


# ── BUG-450 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_an_upload_removes_its_catalog_files(uploads):
    await du.load_catalog("acme", use_llm=False)
    side = uploads / du._CATALOG_DIRNAME
    assert any(f.name.startswith("orders.csv.") for f in side.iterdir())

    du.remove_catalog_files(str(uploads / "orders.csv"))
    assert not any(f.name.startswith("orders.csv.") for f in side.iterdir())


@pytest.mark.asyncio
async def test_deleting_through_the_file_service_leaves_no_derived_copy(uploads):
    """The user-facing delete removes every copy of the data at once, without
    waiting for the account's next catalog build (which may never come)."""
    from shared.file_service import FileService

    (uploads / "customers.csv").write_text("order_id,name\n1,ann\n2,bob\n")
    hdr = uploads / du._HEADER_CACHE_DIRNAME
    hdr.mkdir(exist_ok=True)
    (hdr / "orders.csv.json").write_text('{"headers": ["order_id", "amount"], "by_llm": true}')
    await du.load_catalog("acme", use_llm=False)
    side = uploads / du._CATALOG_DIRNAME
    rel = side / du._RELATIONSHIPS_FILE
    rel.write_text('{"orders=orders.csv|1||customers=customers.csv|2": [], "a=a.csv|1||b=b.csv|2": []}')

    assert FileService().delete_file("orders", subdir="acme") is True

    assert not (uploads / "orders.csv").exists()
    assert not any(f.name.startswith("orders.csv") for f in side.iterdir())  # entry + Parquet
    assert not (hdr / "orders.csv.json").exists()
    import json
    assert list(json.loads(rel.read_text())) == ["a=a.csv|1||b=b.csv|2"]
    assert any(f.name.startswith("customers.csv.") for f in side.iterdir())  # others untouched


# ── BUG-451 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heuristic_column_names_are_not_kept_as_final(uploads, monkeypatch):
    (uploads / "raw.csv").write_text("1,2025-01-01,9.5\n2,2025-01-02,7.25\n3,2025-01-03,1.5\n")

    calls = []
    real = du._profile_file

    def counting(conn, obj, table_name, read_fn, use_llm):
        calls.append((obj.name, use_llm))
        return real(conn, obj, table_name, read_fn, use_llm)

    monkeypatch.setattr(du, "_profile_file", counting)
    monkeypatch.setattr(du, "infer_headers_with_llm", lambda *a, **k: None)  # model unavailable

    await du.load_catalog("acme", use_llm=False)  # a dashboard build: heuristic names
    await du.load_catalog("acme", use_llm=True)   # chat: must try the model, not reuse them
    assert ("raw.csv", True) in calls
    assert not (uploads / du._HEADER_CACHE_DIRNAME / "raw.csv.json").exists()
