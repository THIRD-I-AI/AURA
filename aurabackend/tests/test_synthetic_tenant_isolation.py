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

import uuid  # noqa: E402

import pytest  # noqa: E402

from api_gateway import persistence  # noqa: E402
from api_gateway.routers import synthetic  # noqa: E402


def _req(org):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"org_id": org} if org else None),
        headers=types.SimpleNamespace(get=lambda *_a, **_k: None),
    )


# Jobs live in the gateway database now (BUG-430). Each test uses its own org
# names so rows from other tests in the same database never show up.
def _org(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


async def _seed_job(job_id: str, workspace_id: str) -> None:
    await persistence.insert_synthetic_job({
        "job_id": job_id,
        "workspace_id": workspace_id,
        "status": "completed",
        "output_uri": f"file:///tmp/{job_id}.parquet",
        "target_size": "1MB",
        "created_at": 0.0,
        "progress": None,
        "result": None,
        "error": None,
    })


@pytest.mark.asyncio
async def test_list_jobs_scoped_to_caller_workspace():
    org_a, org_b = _org("orgA"), _org("orgB")
    job_a, job_b = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    await _seed_job(job_a, org_a)
    await _seed_job(job_b, org_b)

    resp_a = await synthetic.list_synthetic_jobs(_req(org_a))
    resp_b = await synthetic.list_synthetic_jobs(_req(org_b))

    assert [j["job_id"] for j in resp_a["jobs"]] == [job_a]
    assert [j["job_id"] for j in resp_b["jobs"]] == [job_b]


@pytest.mark.asyncio
async def test_get_job_rejects_cross_tenant_read():
    from fastapi import HTTPException

    org_a, org_b = _org("orgA"), _org("orgB")
    job_a = uuid.uuid4().hex[:12]
    await _seed_job(job_a, org_a)

    # Owner can read it.
    job = await synthetic.get_synthetic_job(job_a, _req(org_a))
    assert job["job_id"] == job_a

    # A different tenant gets the same 404 as a genuinely unknown id --
    # never confirms the job exists under someone else's workspace.
    with pytest.raises(HTTPException) as exc_info:
        await synthetic.get_synthetic_job(job_a, _req(org_b))
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
    org_a = _org("orgA")
    result = await synthetic.synthetic_generate(req, _req(org_a))
    job = await persistence.get_synthetic_job(result["job_id"], org_a)
    assert job is not None and job["workspace_id"] == org_a
