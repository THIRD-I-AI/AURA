"""
Tier-B integration test: DuckDB reads CSVs from real S3-compatible storage (MinIO).

Tier A (no env var) — skips with a clear message.
Tier B (AURA_S3_TEST_ENDPOINT set) — spins up a boto3 client against a live MinIO
instance, writes a CSV via the S3Backend, builds schema context over tenant "acme",
then asserts a GROUP BY query returns the expected aggregated values.

CI lane: storage-s3 job in .github/workflows/ci.yml starts minio/minio via
`docker run` (official image; requires `server /data` command not available in
a services: block) and sets AURA_S3_TEST_ENDPOINT=http://localhost:9000.

Skip gate: AURA_S3_TEST_ENDPOINT is absent on the base backend-test lane so this
module produces "1 skipped" without collection errors — matching the Tier-B pattern
established by test_scheduler_distributed.py / feedback_optional_dep_test_gating.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    not os.getenv("AURA_S3_TEST_ENDPOINT"),
    reason="set AURA_S3_TEST_ENDPOINT to run the real DuckDB-reads-s3 test",
)


def _env(monkeypatch):
    """Wire monkeypatch env vars so the S3Backend + DuckDB httpfs target the test MinIO."""
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AURA_S3_ENDPOINT_URL", os.environ["AURA_S3_TEST_ENDPOINT"])
    monkeypatch.setenv("AURA_S3_BUCKET", os.environ.get("AURA_S3_TEST_BUCKET", "aura-test"))
    monkeypatch.setenv("AURA_S3_ACCESS_KEY_ID", os.environ["AURA_S3_TEST_KEY"])
    monkeypatch.setenv("AURA_S3_SECRET_ACCESS_KEY", os.environ["AURA_S3_TEST_SECRET"])
    monkeypatch.setenv("AURA_S3_URL_STYLE", "path")
    monkeypatch.setenv("AURA_S3_USE_SSL", "false")
    from shared.config import reload_settings
    reload_settings()
    from shared.storage import reset_storage_backend
    reset_storage_backend()


def test_duckdb_reads_csv_from_s3(monkeypatch):
    """End-to-end: write a CSV to MinIO via S3Backend, read it back through DuckDB httpfs."""
    _env(monkeypatch)
    import boto3

    from shared.storage import get_storage_backend

    b = get_storage_backend()

    # Create the test bucket (idempotent — ignore BucketAlreadyOwnedByYou / similar).
    try:
        boto3.client(
            "s3",
            endpoint_url=os.environ["AURA_S3_TEST_ENDPOINT"],
            aws_access_key_id=os.environ["AURA_S3_TEST_KEY"],
            aws_secret_access_key=os.environ["AURA_S3_TEST_SECRET"],
        ).create_bucket(Bucket=os.environ.get("AURA_S3_TEST_BUCKET", "aura-test"))
    except Exception:
        pass  # already exists or race with another test — fine

    b.write("acme", "sales.csv", b"region,revenue\nN,100\nS,200\nN,50\n")

    from shared.data_utils import build_schema_context_cached
    from shared.duckdb_factory import new_connection

    con = new_connection()
    result = asyncio.run(build_schema_context_cached(con, "acme", use_llm=False))
    assert "sales" in result["tables"], (
        f"Expected 'sales' table in schema context; got: {list(result['tables'].keys())}"
    )

    rows = con.execute(
        "SELECT region, SUM(revenue) FROM read_csv_auto('"
        + b.duckdb_uri("acme", "sales.csv")
        + "') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert dict(rows) == {"N": 150, "S": 200}, (
        f"Unexpected aggregation result: {rows}"
    )


def test_etl_preview_and_execute_read_uploaded_file_from_s3(monkeypatch):
    """BUG-035: /etl/preview-source and /etl/execute used to read the local
    tenant upload dir directly, bypassing the S45 StorageBackend abstraction
    entirely -- so a file uploaded under AURA_STORAGE_BACKEND=s3 (which
    /api/v1/upload already writes to S3 correctly) 404'd for both ETL
    endpoints, since nothing with that name ever existed on local disk.
    Drives both routes end-to-end through the real HTTP app against MinIO."""
    _env(monkeypatch)
    import boto3

    try:
        boto3.client(
            "s3",
            endpoint_url=os.environ["AURA_S3_TEST_ENDPOINT"],
            aws_access_key_id=os.environ["AURA_S3_TEST_KEY"],
            aws_secret_access_key=os.environ["AURA_S3_TEST_SECRET"],
        ).create_bucket(Bucket=os.environ.get("AURA_S3_TEST_BUCKET", "aura-test"))
    except Exception:
        pass  # already exists or race with another test -- fine

    from shared.storage import get_storage_backend
    get_storage_backend().write(
        "default", "etl_sales.csv", b"region,revenue\nN,100\nS,200\nN,50\n",
    )

    from fastapi.testclient import TestClient

    from api_gateway.main import app

    # TestClient WITHOUT `with` -- driving the ASGI lifespan leaves non-daemon
    # aiosqlite threads that hang pytest on exit (see test_synthetic_api.py).
    client = TestClient(app)

    r = client.post("/api/v1/etl/preview-source", json={"source_file": "etl_sales.csv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert body["row_count"] == 3
    assert {c["name"] for c in body["columns"]} == {"region", "revenue"}

    r = client.post("/api/v1/etl/execute", json={
        "source_file": "etl_sales.csv",
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
