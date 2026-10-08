"""BUG-361: a failing job's retry recursed into execute_job, which created a new execution
with retry_count=0 each time, so `retry_count < max_retries` never stopped it -- the job
retried forever and held the scheduler worker."""
from __future__ import annotations

import asyncio
import itertools
import types

from scheduler_service.executor import JobExecutor
from scheduler_service.models import JobStatus


class _Repo:
    """In-memory stand-in for SchedulerRepository's execution/log methods."""

    def __init__(self) -> None:
        self.executions: dict = {}
        self.job_updates: list = []
        self._ids = itertools.count(1)

    async def create_execution(self, data):
        ex = types.SimpleNamespace(id=f"ex{next(self._ids)}", started_at=None, **data)
        self.executions[ex.id] = ex
        return ex

    async def update_execution(self, execution_id, updates):
        ex = self.executions[execution_id]
        for k, v in updates.items():
            setattr(ex, k, v)
        return ex

    async def get_execution(self, execution_id):
        return self.executions.get(execution_id)

    async def update_job(self, job_id, updates):
        self.job_updates.append(updates)

    async def add_log(self, entry):
        pass


def _job(max_retries: int):
    return types.SimpleNamespace(
        id="job1", name="nightly", connection_id="c1", query="SELECT 1", timeout_seconds=5,
        max_retries=max_retries, retry_delay_seconds=0, schedule_type="daily", is_active=True,
        schedule_config={"hour": 0, "minute": 0})


def test_a_failing_job_stops_after_max_retries_in_one_execution():
    repo = _Repo()
    executor = JobExecutor(repo)
    attempts = 0

    async def _always_fails(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts > 20:
            raise SystemExit("retried without bound")
        raise RuntimeError("database down")

    executor._execute_query = _always_fails
    executor.notifications.notify_job_failure = lambda **kw: asyncio.sleep(0)

    result = asyncio.run(asyncio.wait_for(executor.execute_job(_job(max_retries=2)), timeout=10))

    assert attempts == 3  # the first try plus 2 retries
    assert len(repo.executions) == 1
    assert result.status == JobStatus.FAILED and result.retry_count == 2


def test_a_retry_that_succeeds_completes_the_same_execution():
    repo = _Repo()
    executor = JobExecutor(repo)
    calls = 0

    async def _fails_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("blip")
        return {"row_count": 1, "columns": ["x"], "rows": [[1]]}

    executor._execute_query = _fails_once

    result = asyncio.run(executor.execute_job(_job(max_retries=2)))

    assert calls == 2 and len(repo.executions) == 1
    assert result.status == JobStatus.SUCCESS and result.retry_count == 1


def test_a_job_that_used_up_its_retries_moves_to_its_next_slot():
    # BUG-362: only success advanced next_execution_time, so a job that kept failing
    # stayed due and fired again on every worker tick.
    repo = _Repo()
    executor = JobExecutor(repo)

    async def _fails(**kwargs):
        raise RuntimeError("database down")

    executor._execute_query = _fails
    executor.notifications.notify_job_failure = lambda **kw: asyncio.sleep(0)

    asyncio.run(executor.execute_job(_job(max_retries=0)))

    assert repo.job_updates and repo.job_updates[-1].get("next_execution_time") is not None
