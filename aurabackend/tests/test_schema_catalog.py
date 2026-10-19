"""The catalog profiles each upload once and loads only the tables a query needs."""
from __future__ import annotations

import duckdb
import pytest

import shared.data_utils as du
from shared.cache import schema_cache


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    """A local storage backend rooted in tmp_path, with two related files."""
    from shared.storage.local import LocalBackend

    backend = LocalBackend(str(tmp_path))
    monkeypatch.setattr("shared.storage.get_storage_backend", lambda: backend)
    folder = tmp_path / backend.tenant_dir("acme").name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "customers.csv").write_text("customer_id,name\n1,Ann\n2,Bo\n")
    (folder / "orders.csv").write_text("order_id,customer_id,amount\n10,1,5.5\n11,2,7.0\n")
    return folder


@pytest.fixture(autouse=True)
async def _empty_cache():
    await schema_cache.clear_prefix(du.SCHEMA_CACHE_PREFIX)
    yield
    await schema_cache.clear_prefix(du.SCHEMA_CACHE_PREFIX)


def _count_profiles(monkeypatch):
    calls = []
    real = du._profile_file

    def counting(conn, obj, table_name, read_fn, use_llm):
        calls.append(obj.name)
        return real(conn, obj, table_name, read_fn, use_llm)

    monkeypatch.setattr(du, "_profile_file", counting)
    return calls


@pytest.mark.asyncio
async def test_the_catalog_describes_files_and_their_relationships(uploads):
    catalog = await du.load_catalog("acme", use_llm=False)
    assert set(catalog["tables"]) == {"customers", "orders"}
    assert any(r["from_table"] == "orders" and r["to_table"] == "customers" for r in catalog["relationships"])


@pytest.mark.asyncio
async def test_only_the_requested_tables_are_loaded(uploads):
    catalog = await du.load_catalog("acme", use_llm=False)
    con = duckdb.connect(":memory:")
    await du.materialize_tables(con, catalog, {"orders"})
    loaded = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert loaded == {"orders"}


@pytest.mark.asyncio
async def test_after_a_restart_unchanged_files_are_not_profiled_again(uploads, monkeypatch):
    await du.load_catalog("acme", use_llm=False)
    await schema_cache.clear_prefix(du.SCHEMA_CACHE_PREFIX)  # what a restart does to memory

    calls = _count_profiles(monkeypatch)
    catalog = await du.load_catalog("acme", use_llm=False)
    assert calls == []
    assert set(catalog["tables"]) == {"customers", "orders"} and catalog["relationships"]


@pytest.mark.asyncio
async def test_a_new_upload_profiles_only_the_new_file(uploads, monkeypatch):
    await du.load_catalog("acme", use_llm=False)
    (uploads / "products.csv").write_text("product_id,label\n1,x\n")

    calls = _count_profiles(monkeypatch)
    catalog = await du.load_catalog("acme", use_llm=False)
    assert calls == ["products.csv"]
    assert set(catalog["tables"]) == {"customers", "orders", "products"}


@pytest.mark.asyncio
async def test_a_changed_file_is_profiled_again(uploads, monkeypatch):
    import os
    import time

    await du.load_catalog("acme", use_llm=False)
    target = uploads / "orders.csv"
    target.write_text("order_id,customer_id,amount,note\n10,1,5.5,a\n")
    os.utime(target, (time.time() + 5, time.time() + 5))

    calls = _count_profiles(monkeypatch)
    catalog = await du.load_catalog("acme", use_llm=False)
    assert calls == ["orders.csv"]
    assert [c["name"] for c in catalog["tables"]["orders"]["columns"]][-1] == "note"


@pytest.mark.asyncio
async def test_the_full_loader_still_loads_everything(uploads):
    con = duckdb.connect(":memory:")
    result = await du.build_schema_context_cached(con, "acme", use_llm=False)
    assert set(result["tables"]) == {"customers", "orders"}
    assert con.execute("SELECT SUM(amount) FROM orders").fetchone()[0] == pytest.approx(12.5)
