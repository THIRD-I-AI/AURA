"""Audit round 8 -- scheduler service regressions (BUG-501..504).

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
from scheduler_service.worker import SchedulerWorker


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


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _daily_job(repo, **overrides):
    data = {
        "id": f"j_{uuid.uuid4().hex[:8]}", "name": "nightly", "connection_id": "c1",
        "query": "SELECT 1", "schedule_type": ScheduleType.DAILY,
        "schedule_config": {"hour": 9, "minute": 0}, "max_retries": 0,
        "retry_delay_seconds": 1, "timeout_seconds": 5, "is_active": True,
        "next_execution_time": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    data.update(overrides)
    return await repo.create_job(data)


# ── BUG-501 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_once_job_created_through_the_api_is_due(repo):
    created = await sched_main.create_job(sched_main.CreateJobRequest(
        name="one-shot", connection_id="c1", query="SELECT 1", schedule_type="once"))

    due = await repo.get_jobs_to_execute(_now_naive() + timedelta(seconds=1))
    assert created.next_execution_time is not None
    assert [j.id for j in due] == [created.id]


@pytest.mark.asyncio
async def test_a_resumed_once_job_that_never_ran_is_due(repo):
    job = await _daily_job(repo, schedule_type=ScheduleType.ONCE, is_active=False,
                           next_execution_time=None)

    await sched_main.resume_job(job.id)

    due = await repo.get_jobs_to_execute(_now_naive() + timedelta(seconds=1))
    assert [j.id for j in due] == [job.id]


@pytest.mark.asyncio
async def test_a_resumed_daily_job_gets_its_next_slot(repo):
    job = await _daily_job(repo, is_active=False, next_execution_time=None)

    resumed = await sched_main.resume_job(job.id)

    assert resumed.next_execution_time is not None
    assert (resumed.next_execution_time.hour, resumed.next_execution_time.minute) == (9, 0)


@pytest.mark.asyncio
async def test_a_once_job_does_not_fire_again_after_it_ran(repo, executor):
    created = await sched_main.create_job(sched_main.CreateJobRequest(
        name="one-shot", connection_id="c1", query="SELECT 1", schedule_type="once"))

    async def _ok(**kwargs):
        return {"row_count": 1, "columns": ["x"], "rows": [[1]]}

    executor._execute_query = _ok
    await executor.execute_job(await repo.get_job(created.id))

    assert (await repo.get_job(created.id)).next_execution_time is None
    assert await repo.get_jobs_to_execute(_now_naive() + timedelta(days=400)) == []


# ── BUG-502 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failing_attempt_reschedules_its_retry_instead_of_sleeping(repo, executor):
    job = await _daily_job(repo, max_retries=3, retry_delay_seconds=3600)
    attempts = 0

    async def _down(**kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("connection refused")

    executor._execute_query = _down

    # Old code slept retry_delay_seconds (an hour) inside the worker tick.
    first = await asyncio.wait_for(executor.execute_job(job), timeout=5)

    assert attempts == 1
    assert first.status == JobStatus.PENDING and first.retry_count == 1
    stored = await repo.get_job(job.id)
    expected = datetime.now(timezone.utc) + timedelta(seconds=3600)
    assert abs((stored.next_execution_time - expected).total_seconds()) < 30

    # The next due tick continues the same execution rather than starting a new one.
    second = await asyncio.wait_for(executor.execute_job(stored), timeout=5)
    assert second.id == first.id and second.retry_count == 2
    assert len(await repo.list_executions(job_id=job.id)) == 1


@pytest.mark.asyncio
async def test_retries_stop_at_max_retries_across_ticks(repo, executor):
    job = await _daily_job(repo, max_retries=2, retry_delay_seconds=3600)

    async def _down(**kwargs):
        raise RuntimeError("connection refused")

    executor._execute_query = _down
    result = None
    for _ in range(3):
        result = await asyncio.wait_for(executor.execute_job(await repo.get_job(job.id)), timeout=5)

    assert result.status == JobStatus.FAILED and result.retry_count == 2
    assert len(await repo.list_executions(job_id=job.id)) == 1
    stored = await repo.get_job(job.id)
    # back on its daily 09:00 slot, not the retry time
    assert (stored.next_execution_time.hour, stored.next_execution_time.minute) == (9, 0)


# BUG-502 review: with retries out of the tick, a downstream job must not run while
# its upstream's retry is outstanding (depends_on = "must succeed first").

def _make_due(when=None):
    return {"next_execution_time": when or datetime.now(timezone.utc) - timedelta(seconds=1)}


@pytest.mark.asyncio
async def test_downstream_waits_for_the_upstream_retry_to_succeed(repo, executor):
    up = await _daily_job(repo, name="A", query="SELECT 'A'", max_retries=1,
                          retry_delay_seconds=3600)
    down = await _daily_job(repo, name="B", query="SELECT 'B'", depends_on=[up.id])
    b_due = (await repo.get_job(down.id)).next_execution_time
    ran = []

    async def _query(**kwargs):
        ran.append(kwargs["query"])
        if kwargs["query"] == "SELECT 'A'" and ran.count("SELECT 'A'") == 1:
            raise RuntimeError("blip")
        return {"row_count": 1, "columns": ["x"], "rows": [[1]]}

    executor._execute_query = _query
    worker = SchedulerWorker(repo, executor)

    # Tick 1: A fails once -> PENDING; B is deferred and stays due.
    await asyncio.wait_for(worker._evaluate_and_execute(), timeout=10)
    assert ran == ["SELECT 'A'"]
    assert (await repo.list_executions(job_id=up.id))[0].status == JobStatus.PENDING
    assert await repo.list_executions(job_id=down.id) == []
    assert (await repo.get_job(down.id)).next_execution_time == b_due

    # Tick 2: A's retry is not due yet; B alone is due and still deferred.
    await asyncio.wait_for(worker._evaluate_and_execute(), timeout=10)
    assert ran == ["SELECT 'A'"]
    assert (await repo.get_job(down.id)).next_execution_time == b_due

    # Tick 3: A's retry comes due and succeeds; B runs after it in the same tick.
    await repo.update_job(up.id, _make_due())
    await asyncio.wait_for(worker._evaluate_and_execute(), timeout=10)
    assert ran == ["SELECT 'A'", "SELECT 'A'", "SELECT 'B'"]
    b_execs = await repo.list_executions(job_id=down.id)
    assert [e.status for e in b_execs] == [JobStatus.SUCCESS]
    assert (await repo.get_job(down.id)).next_execution_time != b_due


@pytest.mark.asyncio
async def test_downstream_stays_due_when_the_upstream_exhausts_its_retries(repo, executor):
    # No documented on-failed-upstream behaviour (on_failure_action is unused), so B is
    # kept due (not skipped/advanced) and logged; it runs once A next succeeds.
    up = await _daily_job(repo, name="A", query="SELECT 'A'", max_retries=0)
    down = await _daily_job(repo, name="B", query="SELECT 'B'", depends_on=[up.id])
    b_due = (await repo.get_job(down.id)).next_execution_time
    ran = []

    async def _query(**kwargs):
        ran.append(kwargs["query"])
        if kwargs["query"] == "SELECT 'A'":
            raise RuntimeError("down")
        return {"row_count": 1, "columns": ["x"], "rows": [[1]]}

    executor._execute_query = _query
    worker = SchedulerWorker(repo, executor)

    await asyncio.wait_for(worker._evaluate_and_execute(), timeout=10)
    await asyncio.wait_for(worker._evaluate_and_execute(), timeout=10)

    assert ran == ["SELECT 'A'"]
    assert (await repo.list_executions(job_id=up.id))[0].status == JobStatus.FAILED
    assert await repo.list_executions(job_id=down.id) == []
    assert (await repo.get_job(down.id)).next_execution_time == b_due


# ── BUG-503 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_schedule_changed_mid_run_is_not_overwritten(repo, executor):
    job = await _daily_job(repo)

    async def _slow_query(**kwargs):
        await sched_main.update_job(job.id, sched_main.UpdateJobRequest(
            schedule_config={"hour": 18, "minute": 30}))
        return {"row_count": 0, "columns": [], "rows": []}

    executor._execute_query = _slow_query
    await executor.execute_job(job)

    stored = await repo.get_job(job.id)
    assert (stored.next_execution_time.hour, stored.next_execution_time.minute) == (18, 30)


@pytest.mark.asyncio
async def test_a_switch_to_once_mid_run_does_not_refire_on_the_old_schedule(repo, executor):
    job = await _daily_job(repo)

    async def _slow_query(**kwargs):
        await repo.update_job(job.id, {"schedule_type": ScheduleType.ONCE,
                                       "next_execution_time": None})
        return {"row_count": 0, "columns": [], "rows": []}

    executor._execute_query = _slow_query
    await executor.execute_job(job)

    assert (await repo.get_job(job.id)).next_execution_time is None


# ── BUG-504 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_attempt_success_records_its_real_duration(repo, executor):
    job = await _daily_job(repo)

    async def _takes_a_second(**kwargs):
        await asyncio.sleep(1.1)
        return {"row_count": 1, "columns": ["x"], "rows": [[1]]}

    executor._execute_query = _takes_a_second
    result = await executor.execute_job(job)

    assert result.status == JobStatus.SUCCESS
    assert result.duration_seconds == 1


@pytest.mark.asyncio
async def test_duration_after_a_retry_excludes_the_retry_delay(repo, executor):
    job = await _daily_job(repo, max_retries=1, retry_delay_seconds=2)
    calls = 0

    async def _fails_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("blip")
        return {"row_count": 1, "columns": ["x"], "rows": [[1]]}

    executor._execute_query = _fails_once
    result = await asyncio.wait_for(executor.execute_job(job), timeout=10)
    if result.status == JobStatus.PENDING:
        result = await executor.execute_job(await repo.get_job(job.id))

    assert result.status == JobStatus.SUCCESS and result.retry_count == 1
    assert result.duration_seconds == 0
    span = (result.completed_at - result.started_at).total_seconds()
    assert result.duration_seconds == int(span)
