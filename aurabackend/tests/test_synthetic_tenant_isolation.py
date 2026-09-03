"""
Tenant-isolation regression for api_gateway/routers/synthetic.py.

_jobs was a bare module-level dict with no tenant field at all --
GET /synthetic/jobs returned every tenant's job list, and
GET /synthetic/jobs/{job_id} had no ownership check, so any authenticated
caller could enumerate/poll another tenant's synthetic-data job (output
URI, schema, size) by guessing its id. Fixed by stamping workspace_id at
job creation and scoping both read routes to the caller's workspace.

Drives the route functions directly against a fake Request carrying a
verified org_id (mirrors tests/test_tenant_isolation.py's `_req` pattern)
rather than the full HTTP stack, since JWT middleware setup is orthogonal
to what this test needs to prove.
"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api_gateway.routers import synthetic  # noqa: E402


def _req(org):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"org_id": org} if org else None),
        headers=types.SimpleNamespace(get=lambda *_a, **_k: None),
    )


@pytest.fixture(autouse=True)
def _clean_jobs():
    synthetic._jobs.clear()
    yield
    synthetic._jobs.clear()


def _seed_job(job_id: str, workspace_id: str) -> None:
    synthetic._jobs[job_id] = {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "status": "completed",
        "output_uri": f"file:///tmp/{job_id}.parquet",
        "target_size": "1MB",
        "created_at": 0.0,
        "progress": None,
        "result": None,
        "error": None,
    }


@pytest.mark.asyncio
async def test_list_jobs_scoped_to_caller_workspace():
    _seed_job("job-a", "orgA")
    _seed_job("job-b", "orgB")

    resp_a = await synthetic.list_synthetic_jobs(_req("orgA"))
    resp_b = await synthetic.list_synthetic_jobs(_req("orgB"))

    assert [j["job_id"] for j in resp_a["jobs"]] == ["job-a"]
    assert [j["job_id"] for j in resp_b["jobs"]] == ["job-b"]


@pytest.mark.asyncio
async def test_get_job_rejects_cross_tenant_read():
    from fastapi import HTTPException

    _seed_job("job-a", "orgA")

    # Owner can read it.
    job = await synthetic.get_synthetic_job("job-a", _req("orgA"))
    assert job["job_id"] == "job-a"

    # A different tenant gets the same 404 as a genuinely unknown id --
    # never confirms the job exists under someone else's workspace.
    with pytest.raises(HTTPException) as exc_info:
        await synthetic.get_synthetic_job("job-a", _req("orgB"))
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_generate_stamps_caller_workspace_on_new_job(monkeypatch):
    # Avoid actually running the background generation thread.
    monkeypatch.setattr(synthetic.asyncio.get_event_loop(), "run_in_executor", lambda *a, **k: None)

    req = synthetic.GenerateRequest(
        schema_def={"name": "t", "columns": [{"name": "id", "dtype": "sequence"}]},
        target_size="1KB",
        output_uri="file:///tmp/out",
    )
    result = await synthetic.synthetic_generate(req, _req("orgA"))
    job = synthetic._jobs[result["job_id"]]
    assert job["workspace_id"] == "orgA"
