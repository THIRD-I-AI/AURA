import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa: I001

from shared.file_service import FileService


def test_list_files_is_scoped_to_subdir(tmp_path, monkeypatch):
    # S45: list_files now reads through the storage backend, which resolves
    # its root from AURA_UPLOADS_ROOT (not the FileService.uploads_path attr).
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    fs = FileService()
    (tmp_path / "orgA").mkdir()
    (tmp_path / "orgB").mkdir()
    (tmp_path / "orgA" / "a.csv").write_text("x")
    (tmp_path / "orgB" / "b.csv").write_text("y")
    names_a = {f["filename"] for f in fs.list_files(subdir="orgA")}
    assert names_a == {"a.csv"}
    assert "b.csv" not in names_a


import asyncio  # noqa: E402
import pathlib  # noqa: E402

import duckdb  # noqa: E402

from shared.data_utils import build_schema_context_cached  # noqa: E402


def test_schema_context_is_tenant_scoped(tmp_path, monkeypatch):
    # S45: the reader takes a tenant (not a dir list) and enumerates datasets
    # via the storage backend, rooted at AURA_UPLOADS_ROOT.
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    (tmp_path / "orgA").mkdir()
    (tmp_path / "orgB").mkdir()
    (tmp_path / "orgA" / "sales.csv").write_text("id,amt\n1,10\n")
    (tmp_path / "orgB" / "secret.csv").write_text("id,ssn\n1,999\n")

    async def run(tenant):
        con = duckdb.connect(":memory:")
        return await build_schema_context_cached(con, tenant, use_llm=False)

    a = asyncio.run(run("orgA"))
    assert "sales" in a["tables"]
    assert "secret" not in a["tables"]


import types  # noqa: E402


def _req(org):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"org_id": org} if org else None))


def test_tenant_upload_dir_is_per_principal(tmp_path, monkeypatch):
    from api_gateway.routers import workspaces
    monkeypatch.setattr(workspaces, "_UPLOADS_ROOT", str(tmp_path))
    a = workspaces.tenant_upload_dir(_req("orgA"))
    b = workspaces.tenant_upload_dir(_req("orgB"))
    assert os.path.basename(a) == "orgA"
    assert os.path.basename(b) == "orgB"
    assert a != b
    # untenanted (dev / no JWT) -> shared default bucket
    assert os.path.basename(workspaces.tenant_upload_dir(_req(None))) == "default"
    # hostile org id cannot escape the uploads root
    h = workspaces.tenant_upload_dir(_req("../../keys"))
    assert os.path.commonpath((os.path.abspath(h), str(tmp_path))) == str(tmp_path)


# ── /files/{id}/profile must not serve another tenant's profile ──────────

def test_file_profile_is_tenant_scoped(tmp_path, monkeypatch):
    """A profile that EXISTS must still 404 when the file is not the caller's.

    GET /files/{file_id} was patched for this bug; GET /files/{file_id}/profile
    was missed and took no `request` at all, so it could not scope even in
    principle. file_id is a filename, not an unguessable id, and DatasetProfile
    carries the column profile INCLUDING sample values -- so an unscoped read
    handed one org another org's data.

    The stub repository below always returns a profile. That is the whole point:
    if the gate is removed, this test gets 200 and the leak is back. A test that
    merely asked for a nonexistent profile would pass either way and prove
    nothing.
    """
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    from shared.storage import reset_storage_backend
    reset_storage_backend()

    # orgB owns the file; the caller (unauthenticated -> "default") does not.
    (tmp_path / "orgB").mkdir()
    (tmp_path / "orgB" / "secret.csv").write_text("salary\n100000\n")

    import api_gateway.routers.files as files_mod

    class _Profile:
        dataset_name = "secret.csv"
        rows_count = 1
        columns_count = 1
        profile = {"salary": {"samples": [100000]}}   # the payload that leaks
        updated_at = None

    class _Repo:
        async def get_dataset_profile(self, file_id):
            return _Profile()                          # always found

    async def _fake_get_repository():
        yield _Repo()

    monkeypatch.setattr(files_mod, "get_repository", _fake_get_repository)

    from fastapi.testclient import TestClient

    from api_gateway.main import app
    resp = TestClient(app).get("/api/v1/files/secret.csv/profile")

    assert resp.status_code == 404, (
        "profile for another tenant's file was served: "
        f"{resp.status_code} {resp.text[:200]}"
    )
    assert "100000" not in resp.text


# ── /semantic/models must not serve (or let you overwrite) another tenant's ──

def _fresh_metadata_db(tmp_path, monkeypatch):
    """Point metadata_store at a throwaway SQLite file.

    metadata_store.db reads DATABASE_URL at IMPORT time and memoises the engine
    in a module global, so setting the env var here would be too late — an
    earlier test that touched the repository would already have bound the real
    data/metadata.db. Patch the attribute and clear the memo instead.
    """
    from metadata_store import db as db_mod
    monkeypatch.setattr(db_mod, "DATABASE_URL",
                        f"sqlite+aiosqlite:///{(tmp_path / 'meta.db').as_posix()}")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_session_factory", None)
    return db_mod


def test_semantic_models_are_tenant_scoped(tmp_path, monkeypatch):
    """orgA must not read, and must not silently OVERWRITE, orgB's model.

    list_semantic_models() was a bare select(SemanticModel) with no WHERE, and
    the table had no tenant column to filter on, so GET /semantic/models
    returned every tenant's models — field names, expressions and descriptions
    derived from their data.

    The write half matters as much as the read: upsert scopes its lookup by
    workspace_id, so posting a foreign model_id misses and creates a NEW row
    under the caller's own workspace rather than editing the victim's row.
    """
    db_mod = _fresh_metadata_db(tmp_path, monkeypatch)
    from metadata_store.repository import MetadataRepository

    async def scenario():
        await db_mod.init_db()
        async for session in db_mod.get_session():
            repo = MetadataRepository(session)
            common = dict(description=None, source={}, tags=[], fields=[])
            await repo.upsert_semantic_model(
                model_id="m-a", name="A-model", workspace_id="orgA", **common)
            await repo.upsert_semantic_model(
                model_id="m-b", name="B-model", workspace_id="orgB", **common)

            seen_a = {m.name for m in await repo.list_semantic_models("orgA")}
            leaked = await repo.get_semantic_model("m-b", "orgA")

            # orgA posts orgB's id — must not mutate orgB's row, and must not
            # blow up on the global primary key (a 500 would still confirm the
            # id exists somewhere).
            hijack = await repo.upsert_semantic_model(
                model_id="m-b", name="HIJACKED", workspace_id="orgA", **common)
            assert hijack.id != "m-b", "foreign id was reused, not re-minted"
            after_b = {m.name for m in await repo.list_semantic_models("orgB")}

            # An unscoped caller sees pre-tenanting rows only, never everything.
            unscoped = {m.name for m in await repo.list_semantic_models(None)}
            await session.close()
            return seen_a, leaked, after_b, unscoped

    seen_a, leaked, after_b, unscoped = asyncio.run(scenario())

    assert seen_a == {"A-model"}, f"orgA saw another tenant's models: {seen_a}"
    assert leaked is None, "get_semantic_model served another tenant's model"
    assert after_b == {"B-model"}, f"orgB's model was overwritten: {after_b}"
    assert unscoped == set(), f"unscoped list fell open: {unscoped}"


def test_semantic_list_route_passes_the_tenant_scope(monkeypatch):
    """The route must hand the repository a workspace id, not call it bare.

    Before this fix none of the four /semantic/* handlers took a Request at all,
    so they could not scope even in principle. The stub below records what it
    was called with: reverting the route to list_semantic_models() raises
    TypeError, and passing None instead of the caller's workspace fails the
    assertion — either way this test goes red.
    """
    import api_gateway.routers.pipelines as pipelines_mod
    from api_gateway.routers.workspaces import DEFAULT_WORKSPACE_ID

    received = {}

    class _Repo:
        async def list_semantic_models(self, workspace_id):
            received["workspace_id"] = workspace_id
            return []

    async def _fake_get_repository():
        yield _Repo()

    monkeypatch.setattr(pipelines_mod, "get_repository", _fake_get_repository)

    from fastapi.testclient import TestClient

    from api_gateway.main import app
    resp = TestClient(app).get("/api/v1/semantic/models")

    assert resp.status_code == 200, resp.text
    assert "workspace_id" in received, "route never scoped the repository call"
    # No JWT in this request -> the unauthenticated default bucket, never None
    # (None would match the pre-tenanting NULL rows of every org).
    assert received["workspace_id"] == DEFAULT_WORKSPACE_ID
