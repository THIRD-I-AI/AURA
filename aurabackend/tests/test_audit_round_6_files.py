"""Regression tests for BUG-461 and BUG-462 (audit round 6, files area)."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from metadata_store.db import Base
from metadata_store.models import DatasetProfile, SchemaColumn


@pytest.fixture()
def backend(tmp_path, monkeypatch):
    from shared.storage.local import LocalBackend

    b = LocalBackend(str(tmp_path))
    monkeypatch.setattr("shared.storage.get_storage_backend", lambda: b)
    return b


@pytest.fixture
async def engine(monkeypatch):
    """In-memory metadata DB wired into metadata_store.db, as the schema
    indexer integration tests do."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import metadata_store.db as db_mod

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "_engine", eng)
    monkeypatch.setattr(db_mod, "_session_factory", factory)
    yield factory
    await eng.dispose()


async def _seed_metadata(tenant, name):
    """What the upload route's background hooks write for one upload."""
    from metadata_store.repository import get_repository
    from shared.schema_indexer import _upsert_columns, schema_source_id

    async for repo in get_repository():
        await repo.upsert_dataset_profile(
            file_id=name, dataset_name=name,
            profile={"columns": [{"name": "email"}], "sample_data": [{"email": "ann@example.com"}]},
            rows_count=1, columns_count=1, workspace_id=tenant,
        )
        break
    await _upsert_columns(schema_source_id(tenant, name), [{
        "table_name": os.path.splitext(name)[0], "column_name": "email", "data_type": "VARCHAR",
        "is_nullable": True, "ordinal_position": 1, "sample_values": ["ann@example.com"],
    }])


async def _leftovers(factory):
    async with factory() as s:
        profiles = (await s.execute(select(DatasetProfile))).scalars().all()
        cols = (await s.execute(select(SchemaColumn))).scalars().all()
    return len(profiles), len(cols)


def _request(tenant):
    return SimpleNamespace(state=SimpleNamespace(user={"org_id": tenant}))


# ── BUG-461 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bug461_delete_route_removes_profile_and_schema_rows(backend, engine, monkeypatch):
    from api_gateway.routers import files as files_router
    from shared.file_service import FileService

    tenant = "0b5c1f2e-acme"
    backend.write(tenant, "customers.csv", b"email\nann@example.com\n")
    await _seed_metadata(tenant, "customers.csv")
    assert await _leftovers(engine) == (1, 1)

    monkeypatch.setattr(files_router, "file_service", FileService())
    resp = await files_router.delete_file("customers.csv", _request(tenant))

    assert resp["status"] == "success"
    assert backend.list(tenant) == []
    # The sample rows of a deleted file must not outlive it in the metadata DB.
    assert await _leftovers(engine) == (0, 0)


@pytest.mark.asyncio
async def test_bug461_semantic_model_cannot_be_built_from_a_deleted_file(backend, engine, monkeypatch):
    from fastapi import HTTPException

    from api_gateway.routers import files as files_router
    from api_gateway.routers import pipelines as pipelines_router
    from shared.file_service import FileService

    tenant = "0b5c1f2e-acme"
    backend.write(tenant, "customers.csv", b"email\nann@example.com\n")
    await _seed_metadata(tenant, "customers.csv")
    monkeypatch.setattr(files_router, "file_service", FileService())
    await files_router.delete_file("customers.csv", _request(tenant))

    if pipelines_router.semantic_builder is None or pipelines_router.get_repository is None:
        pytest.skip("semantic builder unavailable")
    req = SimpleNamespace(state=SimpleNamespace(user={"org_id": tenant}), headers={})
    try:
        resp = await pipelines_router.auto_generate_model_from_file("customers.csv", req)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        assert resp.get("status") != "success", resp


@pytest.mark.asyncio
async def test_bug461_expired_verify_upload_metadata_is_purged(tmp_path, engine):
    from shared.file_service import purge_upload_metadata
    from shared.upload_retention import expire_verify_uploads

    tenant = "0b5c1f2e-acme"
    folder = tmp_path / tenant
    folder.mkdir()
    name = "verify_0123abcd_sales.csv"
    f = folder / name
    f.write_text("email\nann@example.com\n")
    old = time.time() - 48 * 3600
    os.utime(f, (old, old))
    await _seed_metadata(tenant, name)

    removed: list = []
    assert expire_verify_uploads(str(tmp_path), removed=removed) == 1
    assert removed == [(tenant, name)]
    for t, n in removed:
        await purge_upload_metadata(t, n)
    assert await _leftovers(engine) == (0, 0)


# ── BUG-462 ──────────────────────────────────────────────────────────


def test_bug462_full_name_delete_spares_files_whose_stem_is_that_name(backend):
    from shared.file_service import FileService

    backend.write("acme", "sales.csv", b"a\n1\n")
    backend.write("acme", "sales.csv.parquet", b"PAR1")
    backend.write("acme", "sales.csv.json", b"[]")

    assert FileService().delete_file("sales.csv", subdir="acme") is True

    assert sorted(o.name for o in backend.list("acme")) == ["sales.csv.json", "sales.csv.parquet"]


def test_bug462_ambiguous_stem_deletes_nothing(backend):
    from shared.file_service import FileService

    backend.write("acme", "sales.csv", b"a\n1\n")
    backend.write("acme", "sales.json", b"[]")

    assert FileService().delete_file("sales", subdir="acme") is False
    assert sorted(o.name for o in backend.list("acme")) == ["sales.csv", "sales.json"]
    # An unambiguous stem still resolves.
    backend.write("acme", "orders.csv", b"a\n1\n")
    assert FileService().delete_file("orders", subdir="acme") is True
    assert "orders.csv" not in {o.name for o in backend.list("acme")}


def test_bug462_get_file_info_uses_the_same_matching_rule(backend):
    from shared.file_service import FileService

    backend.write("acme", "sales.csv", b"a\n1\n")
    backend.write("acme", "sales.csv.json", b"[]")
    info = FileService().get_file_info("sales.csv.json", subdir="acme")
    assert info is not None and info["filename"] == "sales.csv.json"
    assert FileService().get_file_info("sales", subdir="acme") is not None
    backend.write("acme", "sales.json", b"[]")
    assert FileService().get_file_info("sales", subdir="acme") is None
