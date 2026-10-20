"""BUG-430: a synthetic-data job is readable from any gateway worker, not only the one that made it."""
from __future__ import annotations

import importlib
import types
import uuid

import pytest

from api_gateway import persistence


def _req(org):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"org_id": org}),
        headers=types.SimpleNamespace(get=lambda *_a, **_k: None),
    )


@pytest.mark.asyncio
async def test_a_job_made_by_one_worker_is_found_by_another(monkeypatch):
    from api_gateway.routers import synthetic

    org = f"org-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(synthetic.asyncio.get_event_loop(), "run_in_executor", lambda *a, **k: None)
    req = synthetic.GenerateRequest(
        schema_def={"name": "t", "columns": [{"name": "id", "dtype": "sequence"}]},
        target_size="1KB", output_uri="file:///tmp/out",
    )
    job_id = (await synthetic.synthetic_generate(req, _req(org)))["job_id"]

    # A second worker is a separate process: nothing the first one holds in memory.
    other_worker = importlib.reload(synthetic)
    job = await other_worker.get_synthetic_job(job_id, _req(org))
    assert job["job_id"] == job_id and job["status"] == "queued"
    listed = await other_worker.list_synthetic_jobs(_req(org))
    assert [j["job_id"] for j in listed["jobs"]] == [job_id]


@pytest.mark.asyncio
async def test_progress_and_completion_are_recorded():
    org, job_id = f"org-{uuid.uuid4().hex[:8]}", uuid.uuid4().hex[:12]
    await persistence.insert_synthetic_job({
        "job_id": job_id, "workspace_id": org, "status": "queued", "created_at": 1.0,
        "progress": None, "result": None, "error": None,
    })
    await persistence.update_synthetic_job(job_id, {"status": "running", "progress": {"pct": 50}}, 2.0)
    await persistence.update_synthetic_job(job_id, {"status": "completed", "result": {"files": 3}}, 3.0)

    job = await persistence.get_synthetic_job(job_id, org)
    assert job["status"] == "completed" and job["progress"] == {"pct": 50} and job["result"] == {"files": 3}
    assert await persistence.get_synthetic_job(job_id, "someone-else") is None
