"""
Synthetic Data API — end-to-end contract tests.
================================================
Exercises the full HTTP surface of api_gateway/routers/synthetic.py through
the real ASGI app with TestClient:

  * POST /api/v1/synthetic/plan       — dry-run rows/files/bytes plan
  * POST /api/v1/synthetic/generate   — launch background job, poll to done
  * GET  /api/v1/synthetic/jobs       — job list
  * GET  /api/v1/synthetic/jobs/{id}  — poll one job (+ 404 path)
  * validation: bad dtype -> 400, bad size string -> 400

The job runs in a worker thread off the event loop; the test polls the
job endpoint until it reports completed, then verifies the on-disk
Parquet output and manifest match what the job claims.
"""
import glob
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

_API = "/api/v1/synthetic"

# A compact but non-trivial enterprise-like schema: every dtype family.
_SCHEMA = {
    "name": "orders",
    "columns": [
        {"name": "order_id", "dtype": "sequence"},
        {"name": "user_id", "dtype": "int", "dist": "zipf", "zipf_a": 1.5},
        {"name": "amount", "dtype": "float", "dist": "lognormal",
         "mean": 3.0, "std": 1.0, "decimals": 2},
        {"name": "region", "dtype": "category",
         "categories": ["US", "EU", "APAC"], "weights": [0.5, 0.3, 0.2]},
        {"name": "status", "dtype": "category",
         "categories": ["ok", "pending", "failed"], "null_rate": 0.05},
        {"name": "ts", "dtype": "timestamp"},
        {"name": "session", "dtype": "uuid"},
    ],
}


@pytest.fixture()
def client():
    from api_gateway.main import app
    with TestClient(app) as c:   # `with` runs lifespan (DB init etc.)
        yield c


# ── /plan ───────────────────────────────────────────────────────────
def test_plan_dry_run_returns_rows_files_bytes(client):
    r = client.post(f"{_API}/plan", json={"schema": _SCHEMA, "target_size": "1TB"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    plan = body["plan"]
    # 1 TB at a realistic ~30 B/row is tens of billions of rows, many files.
    assert plan["total_rows"] > 1_000_000_000
    assert plan["n_files"] > 100
    assert plan["target_bytes"] == 1_000_000_000_000
    # schema echoed back with all 7 columns
    assert len(body["schema"]["columns"]) == 7


def test_plan_rejects_bad_dtype(client):
    bad = {"name": "x", "columns": [{"name": "c", "dtype": "not_a_type"}]}
    r = client.post(f"{_API}/plan", json={"schema": bad, "target_size": "1MB"})
    assert r.status_code == 400
    assert "dtype" in r.json()["detail"]


def test_plan_rejects_bad_size(client):
    r = client.post(f"{_API}/plan", json={"schema": _SCHEMA, "target_size": "banana"})
    assert r.status_code == 400


# ── /generate + poll ─────────────────────────────────────────────────
def test_generate_job_runs_to_completion(client, tmp_path):
    out = tmp_path / "orders_ds"
    r = client.post(f"{_API}/generate", json={
        "schema": _SCHEMA,
        "target_size": "8MB",
        "output_uri": str(out),
        "seed": 42,
        "chunk_rows": 100_000,
        "file_target_bytes": 1 * 10**6,   # 1MB/file vs 8MB target -> multiple files
    })
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "queued"

    # Poll to terminal state.
    deadline = time.time() + 120
    job = None
    while time.time() < deadline:
        jr = client.get(f"{_API}/jobs/{job_id}")
        assert jr.status_code == 200
        job = jr.json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)

    assert job is not None and job["status"] == "completed", job
    res = job["result"]
    assert res["n_files"] >= 2                      # multi-file run
    assert res["total_rows"] > 0
    assert 0.8 <= res["total_bytes"] / 8_000_000 <= 1.25   # calibrated near target

    # On-disk truth matches the job's claim.
    pq_files = sorted(glob.glob(str(out / "*.parquet")))
    assert len(pq_files) == res["n_files"]
    manifest = out / "_manifest.json"
    assert manifest.exists()
    m = json.loads(manifest.read_text())
    assert m["seed"] == 42
    assert m["result"]["total_rows"] == res["total_rows"]

    # Row count read back from Parquet equals the reported total.
    import pyarrow.parquet as pq
    total = sum(pq.ParquetFile(f).metadata.num_rows for f in pq_files)
    assert total == res["total_rows"]


def test_jobs_list_includes_created_job(client, tmp_path):
    out = tmp_path / "small_ds"
    r = client.post(f"{_API}/generate", json={
        "schema": _SCHEMA, "target_size": "2MB", "output_uri": str(out), "seed": 1,
        "chunk_rows": 100_000,
    })
    job_id = r.json()["job_id"]
    lr = client.get(f"{_API}/jobs")
    assert lr.status_code == 200
    ids = {j["job_id"] for j in lr.json()["jobs"]}
    assert job_id in ids


def test_job_404_for_unknown_id(client):
    r = client.get(f"{_API}/jobs/deadbeefcafe")
    assert r.status_code == 404


def test_generate_rejects_bad_size_before_job(client, tmp_path):
    r = client.post(f"{_API}/generate", json={
        "schema": _SCHEMA, "target_size": "not_a_size", "output_uri": str(tmp_path / "x"),
    })
    assert r.status_code == 400
