"""Counterfactual job state must survive a process restart and stay
tenant-isolated — mirrors test_dashboards_persistence.py / test_lineage_router.py.

The old ``_jobs`` module dict in counterfactual_service/main.py broke the
moment a second gateway replica existed (a job POSTed to replica A 404'd when
polled on replica B, since each replica had its own process-local dict) and
leaked full result artifacts for the life of the process (no eviction). Job
state now lives in api_gateway.persistence (CounterfactualJobRow); see
create_counterfactual_job / update_counterfactual_job / get_counterfactual_job.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway import persistence
from counterfactual_service.main import _new_job, get_job


@pytest.fixture(autouse=True)
def _fresh_engine():
    """Rebind the async engine per test — mirrors tests/test_lineage_router.py.

    pytest-asyncio gives each test its own event loop while the module-level
    engine stays bound to the first one; without this the second async test
    in this file deadlocks with no output. Deliberately does NOT call
    close_database() (see test_lineage_router.py's docstring for why) —
    conftest's pytest_sessionfinish disposes whatever engine is live when the
    whole suite exits.
    """
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    yield


def _user(sub: str = "job-tester", org: str = "org-a") -> dict:
    return {"sub": sub, "org_id": org}


@pytest.mark.asyncio
async def test_job_survives_a_simulated_process_restart():
    """THE regression: job state must be rows, not a Python dict.

    Writes a job, then rebinds the persistence engine mid-test — a fresh
    engine object bound to the SAME sqlite file, exactly what a real process
    restart (or polling a second replica) sees. If the job were only
    readable through the in-memory object that wrote it, this is where it
    would disappear.
    """
    job_id = await _new_job("ca", "org-a")
    await persistence.update_counterfactual_job(
        job_id, state="succeeded", artifact={"ok": True},
    )

    # Simulate a process restart / a second replica: null the module-level
    # engine + session factory WITHOUT disposing them (matches the autouse
    # fixture above — disposing mid-test hangs the aiosqlite worker thread).
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False

    result = await get_job(job_id, user=_user())
    assert result["job_id"] == job_id
    assert result["state"] == "succeeded"
    assert result["artifact"] == {"ok": True}
    assert result["error"] is None


@pytest.mark.asyncio
async def test_unknown_job_id_returns_404():
    with pytest.raises(HTTPException) as exc_info:
        await get_job("ca_doesnotexist", user=_user())
    assert exc_info.value.status_code == 404
    assert "ca_doesnotexist" in exc_info.value.detail


@pytest.mark.asyncio
async def test_cross_tenant_read_returns_404_not_the_job():
    """A cross-tenant read must be indistinguishable from a missing job —
    the deliberate security property documented on main.get_job."""
    owner = _user(sub="owner", org="org-owner")
    job_id = await _new_job("ca", "org-owner")
    await persistence.update_counterfactual_job(
        job_id, state="succeeded", artifact={"secret": 1},
    )

    intruder = _user(sub="intruder", org="org-other")
    with pytest.raises(HTTPException) as exc_info:
        await get_job(job_id, user=intruder)
    assert exc_info.value.status_code == 404
    # Same shape as the unknown-id case — must not confirm the id exists.
    assert exc_info.value.detail == f"job {job_id} not found"

    # The owner can still read it — the check is tenant-scoped, not broken.
    owned = await get_job(job_id, user=owner)
    assert owned["state"] == "succeeded"
    assert owned["artifact"] == {"secret": 1}
