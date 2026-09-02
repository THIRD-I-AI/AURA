"""
Test: file upload writes a real dataset profile (BUG-030).

Before this fix, upload_universal (api_gateway/routers/files.py) never
called upsert_dataset_profile -- only the external-DB connector sync
(connections.py) and the pipeline semantic-model route did -- so
GET /files/{file_id}/profile always 404'd for a plain-uploaded file, even
though the read side was fully implemented. Found live via
scripts/verify_live_deployment.py's file_upload_profile check.

Monkeypatches api_gateway.routers.files.get_repository directly (a
recording fake, no real DB/engine/lifespan involved) rather than exercising
metadata_store's real async SQLite engine end-to-end: doing so hit two
genuine, pre-existing test-infrastructure issues unrelated to this fix's
correctness --
  1. A prior sync TestClient test in the same pytest process leaves
     metadata_store's module-level engine's aiosqlite connections bound to
     a now-closed event loop (that request's own short-lived one), so
     reusing it raises "Event loop is closed" from a background thread --
     the same aiosqlite/event-loop class of issue BUG-008 already fixed
     elsewhere.
  2. httpx.ASGITransport does not drive FastAPI's lifespan, so
     api_gateway/main.py's startup call to metadata_store.db.init_db()
     never runs -- a fresh/reset engine has no tables until a test calls
     init_db() itself.
Both are real findings worth knowing if a future test touches
metadata_store's real engine from an async test, but neither is this fix's
concern -- get_file_schema (used by the new hook) and upsert_dataset_profile
(what it calls) are each already covered by their own existing tests
(test_pipeline_execution.py's BUG-020 coverage; test_metadata_store.py).
This test's job is only to prove upload_universal's hook actually calls
upsert_dataset_profile with the right arguments, which a recording fake
proves directly and robustly.

Also monkeypatches shared.tasks.fire_and_forget itself (rather than
filtering the module-level `_background_tasks` set by name after the
fact): a sibling test in the same file/process that also POSTs to
/api/v1/upload dispatches its own real (unpatched) dataset-profile task
into that SAME global set under the same "dataset-profile-*" name prefix,
so a by-name filter can pick up the wrong task depending on run order --
this fully sidesteps that by capturing and awaiting only THIS test's own
coroutine directly.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.asyncio
async def test_upload_calls_upsert_dataset_profile(tmp_path, monkeypatch):
    """A plain upload must call upsert_dataset_profile with the file's real
    schema -- the wiring GET /files/{file_id}/profile depends on to ever
    find anything for a plain-uploaded file."""
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()

    # api_gateway.routers.workspaces._UPLOADS_ROOT is read from the env var
    # once at module import time, not per-request -- fine in production
    # (the env var never changes after process start) but means the
    # monkeypatch.setenv above is a no-op for tenant_upload_dir() if this
    # module was already imported (e.g. by an earlier test's own app
    # import) before this test set AURA_UPLOADS_ROOT. Patch the already-
    # -resolved constant directly so the hook's tenant_upload_dir(request)
    # call resolves to this test's own tmp_path, matching where the
    # storage backend (reset fresh above, no caching) actually wrote the
    # file.
    import api_gateway.routers.workspaces as workspaces_module
    monkeypatch.setattr(workspaces_module, "_UPLOADS_ROOT", str(tmp_path))

    calls = []

    class _FakeRepo:
        async def upsert_dataset_profile(self, **kwargs):
            calls.append(kwargs)

    async def _fake_get_repository():
        yield _FakeRepo()

    # The hook in upload_universal does `from metadata_store.repository
    # import get_repository` locally, at call time, inside its own try
    # block -- not the module-level import at the top of files.py (that
    # one only feeds get_file_profile's availability check). Patching the
    # source module's attribute is what a fresh local import picks up.
    import metadata_store.repository as metadata_repository_module
    monkeypatch.setattr(metadata_repository_module, "get_repository", _fake_get_repository)

    # Same local-import pattern for fire_and_forget -- patch the source
    # module. Capture only the dataset-profile coroutine to await directly;
    # let the other hooks' coroutines (schema_indexer, file_metadata,
    # schema_ctx) go uncreated by never calling them -- fire_and_forget's
    # caller always builds the coroutine object right before passing it in,
    # so declining to schedule it here is enough; nothing else references it.
    import shared.tasks as tasks_module
    profile_coro_holder = {}

    def _fake_fire_and_forget(coro, *, name=None):
        if name and name.startswith("dataset-profile-"):
            profile_coro_holder["coro"] = coro
        else:
            coro.close()
        return None

    monkeypatch.setattr(tasks_module, "fire_and_forget", _fake_fire_and_forget)

    import httpx

    from api_gateway.main import app

    file_name = f"profile_test_{uuid.uuid4().hex[:8]}.csv"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/upload",
            files={"file": (file_name, b"id,name,revenue\n1,Alice,100\n2,Bob,250\n", "text/csv")},
        )
        assert r.status_code == 200, r.text

        assert "coro" in profile_coro_holder, "dataset-profile task was never dispatched"
        await profile_coro_holder["coro"]

    assert len(calls) == 1, calls
    call = calls[0]
    assert call["file_id"] == file_name
    assert call["rows_count"] == 2
    assert call["columns_count"] == 3
    names = {c["name"] for c in call["profile"]["columns"]}
    assert names == {"id", "name", "revenue"}
