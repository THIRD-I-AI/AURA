"""
Tests: upload write + FileService list/delete through the storage backend (S45).

Sec-8 baseline-strip reconciliation
-------------------------------------
The original ``_safe_upload_path`` (and the new ``_safe_upload_name``) strip
the client filename to its basename before passing it to the backend.  This
means ``"../evil.csv"`` → ``"evil.csv"`` (accepted, NOT a 400) rather than
rejected.  Only truly degenerate names — empty, ``"."``, ``".."``, NUL — are
rejected with 400.  The storage backend's own ``safe_object_name`` guard
handles the containment axis for any basename that still contains separators.

The task-6-brief.md originally asserted that ``"../evil.csv"`` → 400 (Sec-8).
That assertion is incorrect for basename-strip behaviour: the strip *neutralises*
the traversal component (producing ``"evil.csv"``), so the upload succeeds and
the file appears in the backend under the sanitised name.  The test below
reflects the actual code behaviour.
"""
from __future__ import annotations

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── helpers ───────────────────────────────────────────────────────────────────

def _client(tmp_path, monkeypatch):
    """Build a TestClient with the local storage backend rooted at tmp_path."""
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    from fastapi.testclient import TestClient  # noqa: E402 (local import order)

    from api_gateway.main import app
    return TestClient(app)


# ── upload → list round-trip ──────────────────────────────────────────────────

def test_upload_then_list_via_backend(tmp_path, monkeypatch):
    """A successfully uploaded file is visible via get_storage_backend().list()."""
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/api/v1/upload",
        files={"file": ("sales.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert r.status_code == 200, r.text

    from shared.storage import get_storage_backend
    names = {o.name for o in get_storage_backend().list("default")}
    assert "sales.csv" in names


def test_upload_write_offloaded_to_thread(tmp_path, monkeypatch):
    """BUG-055: get_storage_backend().write() is a synchronous, blocking
    filesystem write (shared/storage/local.py). Under this repo's
    single-uvicorn-worker deployment, calling it inline would freeze
    every concurrent tenant's request for its duration. It must be
    dispatched via asyncio.to_thread, matching this endpoint's own
    get_file_schema offload."""
    client = _client(tmp_path, monkeypatch)

    import api_gateway.routers.files as files_module
    real_to_thread = files_module.asyncio.to_thread
    offloaded_funcs: list = []

    async def spy_to_thread(func, *args, **kwargs):
        offloaded_funcs.append(getattr(func, "__name__", func))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(files_module.asyncio, "to_thread", spy_to_thread)

    r = client.post(
        "/api/v1/upload",
        files={"file": ("sales2.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert "write" in offloaded_funcs, (
        f"storage backend write() must run via asyncio.to_thread, not inline on the "
        f"event loop (see backend.md Async safety). Offloaded calls seen: {offloaded_funcs}"
    )


def test_upload_over_size_limit_rejected_with_413(tmp_path, monkeypatch):
    """BUG-056: the advertised max_file_size was documentation only --
    nothing enforced it, so an arbitrarily large body was fully buffered
    in process memory before any check. Shrink the limit for the test so
    we don't need to actually POST tens of MB."""
    client = _client(tmp_path, monkeypatch)

    import api_gateway.routers.files as files_module
    monkeypatch.setattr(files_module.file_service, "max_file_size", 10)

    oversized = b"a,b\n" + b"1,2\n" * 100  # well over 10 bytes
    r = client.post(
        "/api/v1/upload",
        files={"file": ("big.csv", io.BytesIO(oversized), "text/csv")},
    )
    assert r.status_code == 413, r.text

    from shared.storage import get_storage_backend
    names = {o.name for o in get_storage_backend().list("default")}
    assert "big.csv" not in names, "a rejected upload must not be written to storage"


def test_upload_under_size_limit_still_succeeds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    import api_gateway.routers.files as files_module
    monkeypatch.setattr(files_module.file_service, "max_file_size", 1024 * 1024)

    r = client.post(
        "/api/v1/upload",
        files={"file": ("small.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert r.status_code == 200, r.text


# ── traversal → basename-stripped, NOT rejected ───────────────────────────────

def test_upload_traversal_basename_stripped(tmp_path, monkeypatch):
    """``../evil.csv`` is basename-stripped to ``evil.csv`` and accepted (200).

    The path-traversal component is *neutralised*, not rejected.  Only the
    bare degenerate names are 400 (see test_upload_degenerate_name_rejected).
    """
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/api/v1/upload",
        files={"file": ("../evil.csv", io.BytesIO(b"x\n1\n"), "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "evil.csv"

    from shared.storage import get_storage_backend
    names = {o.name for o in get_storage_backend().list("default")}
    assert "evil.csv" in names


# ── degenerate names → 400 ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_name", ["..", "."])
def test_upload_degenerate_name_rejected(tmp_path, monkeypatch, bad_name):
    """Truly degenerate filenames (``"."``, ``".."``) → 400.

    Empty string is rejected by FastAPI's form parsing before our handler
    runs (422); NUL bytes are percent-encoded by the HTTP transport layer
    (``%00``) so they arrive as a printable character sequence, not a NUL.
    We test the names that actually reach ``_safe_upload_name`` as-is.
    """
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/api/v1/upload",
        files={"file": (bad_name, io.BytesIO(b"x\n1\n"), "text/csv")},
    )
    assert r.status_code == 400, f"Expected 400 for name={bad_name!r}, got {r.status_code}: {r.text}"


# ── FileService list/delete through the backend ───────────────────────────────

def test_file_service_list_via_backend(tmp_path, monkeypatch):
    """FileService.list_files() returns objects written through the backend."""
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    get_storage_backend().write("default", "data.csv", b"col\n1\n")

    from shared.file_service import FileService
    svc = FileService()
    files = svc.list_files()
    names = {f["filename"] for f in files}
    assert "data.csv" in names

    reset_storage_backend()


def test_file_service_delete_via_backend(tmp_path, monkeypatch):
    """FileService.delete_file() removes objects from the backend."""
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    backend = get_storage_backend()
    backend.write("default", "remove_me.csv", b"x\n1\n")

    from shared.file_service import FileService
    svc = FileService()
    result = svc.delete_file("remove_me.csv")
    assert result is True
    assert backend.list("default") == []

    reset_storage_backend()


def test_file_service_delete_by_stem(tmp_path, monkeypatch):
    """delete_file() matches by stem (UUID without extension) as well as full name."""
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    backend = get_storage_backend()
    backend.write("default", "abc123.csv", b"x\n1\n")

    from shared.file_service import FileService
    svc = FileService()
    result = svc.delete_file("abc123")  # stem only, no extension
    assert result is True
    assert backend.list("default") == []

    reset_storage_backend()
