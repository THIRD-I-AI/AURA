"""
Regression test — POST /etl/preview-source path-traversal fix (etl_preview_source).

Old code built `Path(tenant_upload_dir(request)) / source_file` directly from the
request body. pathlib lets an ABSOLUTE right-hand side replace the base outright, so
an input like "/etc/passwd" (or "C:\\Windows\\win.ini" on Windows) escaped the tenant
upload sandbox with no ".." anywhere in the string; plain ".." traversal escaped it
too. The route now reads through the active StorageBackend (BUG-035) — its
`safe_object_name()` rejects the same class of attack (absolute paths, "..",
separators, NUL) at the object-name layer the backend itself enforces, while a
legitimate filename inside the tenant's storage namespace still resolves.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api_gateway.main import app  # noqa: E402

_API = "/api/v1/etl/preview-source"


def test_preview_source_rejects_traversal_and_absolute_but_allows_legit_file(tmp_path, monkeypatch):
    # Isolate the storage backend's root; unauthenticated requests fall back
    # to the "default" tenant bucket (_request_tenant() is None with no JWT
    # middleware active).
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()

    get_storage_backend().write("default", "legit.json", b'[{"a": 1, "b": 2}]')

    # TestClient WITHOUT `with` -- driving the ASGI lifespan leaves non-daemon
    # aiosqlite threads that hang pytest on exit (see test_synthetic_api.py).
    client = TestClient(app)

    # 1. Relative "../" traversal -> rejected.
    r = client.post(_API, json={"source_file": "../../../etc/passwd"})
    assert r.status_code == 400, r.text
    assert "Invalid source filename" in r.json()["detail"]

    # 2. Absolute path -> rejected. THE important case: pathlib's `/` lets an
    # absolute RHS replace the base outright, escaping the sandbox with no ".."
    # anywhere. Old code: Path(upload_dir) / "C:\\Windows\\win.ini" resolves to
    # WindowsPath("C:/Windows/win.ini") -- the join silently discards upload_dir.
    evil_abs = "C:\\Windows\\win.ini" if os.name == "nt" else "/etc/passwd"
    r = client.post(_API, json={"source_file": evil_abs})
    assert r.status_code == 400, r.text
    assert "Invalid source filename" in r.json()["detail"]

    # 3. Legitimate filename inside the tenant upload dir -> not treated as a
    # traversal attempt; the real preview flow runs and succeeds.
    r = client.post(_API, json={"source_file": "legit.json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["row_count"] == 1
