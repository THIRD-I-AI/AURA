"""
BUG-035: /etl/preview-source, /etl/execute, and /etl/natural-language used
to read the local tenant upload dir directly (tenant_upload_dir() + a raw
Path), bypassing the S45 StorageBackend abstraction that /api/v1/upload and
every other S45-migrated read path (data_utils.py, files.py) already route
through. Under AURA_STORAGE_BACKEND=s3 a plain upload lands only in S3, so
these three endpoints would 404 on a file they should be able to see.

test_storage_s3_duckdb.py's test_etl_preview_and_execute_read_uploaded_file_from_s3
is the real end-to-end proof (Tier B, needs MinIO). This file is the Tier A
companion: local mode, no external dependency, confirming the same call path
(get_storage_backend().exists()/duckdb_uri(), not a raw filesystem check)
still works byte-for-byte for the everyday local deployment.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api_gateway.main import app  # noqa: E402


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    get_storage_backend().write(
        "default", "sales.csv", b"region,revenue\nN,100\nS,200\nN,50\n",
    )
    # TestClient WITHOUT `with` -- driving the ASGI lifespan leaves non-daemon
    # aiosqlite threads that hang pytest on exit (see test_synthetic_api.py).
    return TestClient(app)


def test_etl_execute_reads_uploaded_file_via_backend(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/execute", json={
        "source_file": "sales.csv",
        "transforms": [{"type": "aggregate", "config": {
            "group_by": ["region"],
            "aggregations": [{"column": "revenue", "func": "SUM", "alias": "total"}],
        }}],
        "preview_only": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert {row["region"]: row["total"] for row in body["preview"]} == {"N": 150, "S": 200}


def test_etl_execute_unknown_file_404s_not_500(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/execute", json={"source_file": "does_not_exist.csv"})
    assert r.status_code == 404, r.text


def test_etl_natural_language_reads_uploaded_file_via_backend(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/etl/natural-language", json={
        "source_file": "sales.csv",
        "instruction": "sum revenue by region",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # No LLM provider configured in CI -- the honest custom_sql fallback still
    # proves the source file itself was read successfully via the backend.
    assert body["status"] == "success", body
    assert {c["name"] for c in body["schema"]} == {"region", "revenue"}
