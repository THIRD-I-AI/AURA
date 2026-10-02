"""BUG-298: one dead audit child left the cached ProcessPoolExecutor broken for the life
of the process, so every later audit from every tenant failed until a restart.

BUG-299: an audit job was marked succeeded before its ledger append, so a poller could
read a success that a failed append then flipped to failed."""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from counterfactual_service import audit_worker
from counterfactual_service import main as m


def _die(_payload):
    os._exit(1)  # what an OOM kill looks like to the parent


def _ok(payload):
    return {"audit_record_hash": "h", "echo": payload["n"]}


@pytest.fixture
def real_pool(monkeypatch):
    monkeypatch.setattr(audit_worker, "_POOL", None)
    monkeypatch.setenv("AUDIT_POOL_WORKERS", "1")
    yield
    if audit_worker._POOL is not None:
        audit_worker._POOL.shutdown(wait=False, cancel_futures=True)


async def _job(payload, monkeypatch, worker) -> dict:
    monkeypatch.setattr(m, "run_audit_subprocess", worker)
    job_id = m._new_job("audit", "acme")
    await m._run_audit_job_async(job_id, payload)
    return m._jobs.pop(job_id)


def test_an_audit_after_a_dead_child_runs_on_a_fresh_pool(real_pool, monkeypatch):
    async def _no_ledger(result, payload):
        return None

    monkeypatch.setattr(m, "_append_fairness_audit_to_ledger", _no_ledger)

    async def scenario():
        crashed = await _job({"n": 1}, monkeypatch, _die)
        broken_pool_was_dropped = audit_worker._POOL is None
        following = await _job({"n": 2}, monkeypatch, _ok)
        return crashed, broken_pool_was_dropped, following

    crashed, dropped, following = asyncio.run(scenario())

    assert crashed["state"] == "failed"
    assert dropped, "the broken pool must not stay cached"
    assert following["state"] == "succeeded", following.get("error")
    assert following["artifact"]["echo"] == 2


def test_only_the_pool_that_broke_is_discarded(monkeypatch):
    stale, current = ProcessPoolExecutor(max_workers=1), ProcessPoolExecutor(max_workers=1)
    try:
        monkeypatch.setattr(audit_worker, "_POOL", current)
        audit_worker.discard_audit_pool(stale)
        assert audit_worker._POOL is current

        audit_worker.discard_audit_pool(current)
        assert audit_worker._POOL is None
    finally:
        stale.shutdown(wait=False)
        current.shutdown(wait=False)


def test_a_job_is_not_reported_succeeded_before_its_ledger_append(monkeypatch):
    monkeypatch.setattr(m, "get_audit_pool", lambda: None)
    seen = {}

    async def scenario():
        monkeypatch.setattr(m, "run_audit_subprocess", _ok)
        job_id = m._new_job("audit", "acme")

        async def _slow_failing_append(result, payload):
            seen["state_during_append"] = m._jobs[job_id]["state"]
            raise RuntimeError("ledger database is locked")

        monkeypatch.setattr(m, "_append_fairness_audit_to_ledger", _slow_failing_append)
        await m._run_audit_job_async(job_id, {"n": 3})
        return m._jobs.pop(job_id)

    job = asyncio.run(scenario())

    assert seen["state_during_append"] == "running"
    assert job["state"] == "failed"
    assert "artifact" not in job or job.get("artifact") is None


def test_broken_process_pool_is_a_broken_executor():
    from concurrent.futures import BrokenExecutor

    assert issubclass(BrokenProcessPool, BrokenExecutor)
