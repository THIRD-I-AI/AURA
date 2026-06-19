"""
S45 Task 5 — data_utils reads datasets through the storage backend (TDD).

LocalBackend parity: build_schema_context_cached(conn, tenant) must produce
the same schema info as the old upload_dirs approach, keyed on the tenant.
Tenant isolation: two tenants with different files see only their own tables.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _reset_storage_backend():
    """Ensure the global storage backend singleton is reset before and after
    each test so monkeypatched env vars don't leak across test boundaries."""
    from shared.storage import reset_storage_backend

    reset_storage_backend()
    yield
    reset_storage_backend()


def test_build_schema_context_over_local_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    get_storage_backend().write("acme", "sales.csv", b"region,revenue\nN,100\nS,200\n")

    import duckdb

    from shared.data_utils import build_schema_context_cached

    con = duckdb.connect(":memory:")
    result = asyncio.run(build_schema_context_cached(con, "acme", use_llm=False))
    assert "sales" in result["tables"]
    cols = [c["name"] for c in result["tables"]["sales"]["columns"]]
    assert "region" in cols and "revenue" in cols


def test_schema_context_tenant_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    get_storage_backend().write("acme", "a.csv", b"x\n1\n")
    get_storage_backend().write("globex", "b.csv", b"y\n2\n")

    import duckdb

    from shared.data_utils import build_schema_context_cached

    con = duckdb.connect(":memory:")
    acme = asyncio.run(build_schema_context_cached(con, "acme", use_llm=False))
    assert set(acme["tables"]) == {"a"}
