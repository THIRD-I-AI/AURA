"""Audit round 11 -- scheduler service regressions (BUG-543, BUG-544).

Runs the real SchedulerRepository on a per-test SQLite file and the real JobExecutor;
only ``_execute_query`` (the HTTP hop to the separate database service) is replaced.
The endpoint handlers are called as plain coroutines with ``_state`` pointed at the
test repository, so the FastAPI lifespan is never driven.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from scheduler_service import main as sched_main
from scheduler_service.executor import JobExecutor
from scheduler_service.models import JobStatus, ScheduleType
from scheduler_service.repository import SchedulerRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    r = SchedulerRepository(database_url=f"sqlite+aiosqlite:///{tmp_path / f's_{uuid.uuid4().hex}.db'}")
    await r.init_db()
    saved = dict(sched_main._state)
    sched_main._state["repository"] = r
    try:
        yield r
    finally:
        sched_main._state.clear()
        sched_main._state.update(saved)
        await r.engine.dispose()


@pytest_asyncio.fixture
async def executor(repo):
    ex = JobExecutor(repo)

    async def _no_notify(**kwargs):
        return None

    ex.notifications.notify_job_failure = _no_notify
    try:
        yield ex
    finally:
        await ex.http_client.aclose()


async def _daily_job(repo, **overrides):
    data = {
        "id": f"j_{uuid.uuid4().hex[:8]}", "name": "nightly", "connection_id": "c1",
        "query": "SELECT 1", "schedule_type": ScheduleType.DAILY,
        "schedule_config": {"hour": 2, "minute": 0}, "max_retries": 3,
        "retry_delay_seconds": 300, "timeout_seconds": 5, "is_active": True,
        "next_execution_time": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    data.update(overrides)
    return await repo.create_job(data)


async def _down(**kwargs):
    raise RuntimeError("connection refused")


async def _ok(**kwargs):
    return {"row_count": 1, "columns": ["x"], "rows": [[1]]}


async def _fail_once_leaving_a_pending_retry(repo, executor, **job_overrides):
    job = await _daily_job(repo, **job_overrides)
    executor._execute_query = _down
    first = await asyncio.wait_for(executor.execute_job(job), timeout=5)
    assert first.status == JobStatus.PENDING and first.retry_count == 1
    return job, first


# ── BUG-543 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_sweep_leaves_a_pending_retry_for_the_worker(repo, executor):
    job, first = await _fail_once_leaving_a_pending_retry(repo, executor)

    # Service restarts while the retry is waiting for its due time.
    await repo.fail_orphaned_executions(0)

    kept = await repo.get_execution(first.id)
    assert kept.status == JobStatus.PENDING and kept.retry_count == 1

    # The due retry continues the same execution with its budget intact.
    second = await asyncio.wait_for(executor.execute_job(await repo.get_job(job.id)), timeout=5)
    assert second.id == first.id and second.retry_count == 2
    assert len(await repo.list_executions(job_id=job.id)) == 1


@pytest.mark.asyncio
async def test_startup_sweep_still_fails_interrupted_runs(repo, executor):
    job = await _daily_job(repo)
    running = await repo.create_execution({"job_id": job.id, "status": JobStatus.PENDING, "retry_count": 0})
    await repo.update_execution(running.id, {"status": JobStatus.RUNNING,
                                             "started_at": datetime.now(timezone.utc)})
    never_started = await repo.create_execution({"job_id": job.id, "status": JobStatus.PENDING,
                                                 "retry_count": 0})

    assert await repo.fail_orphaned_executions(0) == 2
    assert (await repo.get_execution(running.id)).status == JobStatus.FAILED
    assert (await repo.get_execution(never_started.id)).status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_distributed_sweep_measures_a_retry_attempt_from_its_start(repo, executor):
    # A long-lived execution (created hours ago) whose current retry attempt only just
    # started on a live replica is not an orphan.
    job = await _daily_job(repo)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=5)
    ex = await repo.create_execution({"job_id": job.id, "status": JobStatus.PENDING,
                                      "retry_count": 4, "created_at": long_ago})
    await repo.update_execution(ex.id, {"status": JobStatus.RUNNING,
                                        "started_at": datetime.now(timezone.utc)})

    assert await repo.fail_orphaned_executions(7200) == 0
    assert (await repo.get_execution(ex.id)).status == JobStatus.RUNNING


# ── BUG-544 ─────────────────────────────────────────────────────────

async def _next_slot_starts_a_fresh_execution(repo, executor, job_id, stale_id):
    executor._execute_query = _down
    run = await asyncio.wait_for(executor.execute_job(await repo.get_job(job_id)), timeout=5)
    assert run.id != stale_id
    assert run.status == JobStatus.PENDING and run.retry_count == 1
    assert (await repo.get_execution(stale_id)).status == JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_pause_and_resume_settle_the_dropped_retry(repo, executor):
    job, first = await _fail_once_leaving_a_pending_retry(repo, executor, max_retries=1)

    await sched_main.pause_job(job.id)
    await sched_main.resume_job(job.id)

    stale = await repo.get_execution(first.id)
    assert stale.status == JobStatus.CANCELLED
    assert await repo.list_executions(job_id=job.id, status=JobStatus.PENDING) == []
    await _next_slot_starts_a_fresh_execution(repo, executor, job.id, first.id)


@pytest.mark.asyncio
async def test_schedule_change_settles_the_dropped_retry(repo, executor):
    job, first = await _fail_once_leaving_a_pending_retry(repo, executor, max_retries=1)

    await sched_main.update_job(job.id, sched_main.UpdateJobRequest(
        schedule_config={"hour": 3, "minute": 0}))

    assert (await repo.get_execution(first.id)).status == JobStatus.CANCELLED
    await _next_slot_starts_a_fresh_execution(repo, executor, job.id, first.id)


@pytest.mark.asyncio
async def test_an_edit_that_keeps_the_schedule_keeps_the_retry(repo, executor):
    job, first = await _fail_once_leaving_a_pending_retry(repo, executor)

    await sched_main.update_job(job.id, sched_main.UpdateJobRequest(name="renamed"))

    kept = await repo.get_execution(first.id)
    assert kept.status == JobStatus.PENDING and kept.retry_count == 1


@pytest.mark.asyncio
async def test_delete_settles_the_pending_retry(repo, executor):
    job, first = await _fail_once_leaving_a_pending_retry(repo, executor)

    await sched_main.delete_job(job.id)

    assert (await repo.get_execution(first.id)).status == JobStatus.CANCELLED
