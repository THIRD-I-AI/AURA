"""
Sprint S36 — Background task helper tests.

Tier A (pure Python, no optional deps).

Covers:
  * fire_and_forget retains a strong reference until completion
    (replicates the orphan-task scenario by forcing a GC cycle)
  * Task auto-removes from the tracking set on completion
  * Uncaught exceptions in tasks are logged
  * active_count() reflects in-flight task count
"""
from __future__ import annotations

import asyncio
import gc
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.tasks import _background_tasks, active_count, fire_and_forget


@pytest.mark.asyncio
class TestFireAndForget:
    async def test_basic_execution(self):
        ran = []

        async def work():
            ran.append("done")

        task = fire_and_forget(work())
        await task
        assert ran == ["done"]

    async def test_returns_a_task(self):
        async def noop():
            pass

        task = fire_and_forget(noop())
        assert isinstance(task, asyncio.Task)
        await task

    async def test_named_task(self):
        async def noop():
            pass

        task = fire_and_forget(noop(), name="my-worker")
        assert task.get_name() == "my-worker"
        await task

    async def test_strong_reference_survives_gc(self):
        """Reproduces the orphan-task bug: without strong refs, a task
        whose returned value is dropped can be GC'd before it runs.
        fire_and_forget must keep it alive."""
        finished = asyncio.Event()

        async def slow_work():
            await asyncio.sleep(0.05)
            finished.set()

        # Drop the returned task reference deliberately; force a GC
        # cycle. With raw create_task this can drop the task — with
        # fire_and_forget the module-level set keeps it alive.
        fire_and_forget(slow_work())
        gc.collect()
        gc.collect()

        await asyncio.wait_for(finished.wait(), timeout=1.0)
        assert finished.is_set()

    async def test_task_removed_from_set_on_completion(self):
        async def noop():
            pass

        before = active_count()
        task = fire_and_forget(noop())
        await task
        # Done callback runs synchronously after task completion; one
        # event-loop turn ensures the discard ran.
        await asyncio.sleep(0)
        after = active_count()
        assert after == before, f"task not discarded: before={before}, after={after}"

    async def test_active_count_during_execution(self):
        gate = asyncio.Event()

        async def waiter():
            await gate.wait()

        baseline = active_count()
        task = fire_and_forget(waiter())
        assert active_count() == baseline + 1
        gate.set()
        await task
        await asyncio.sleep(0)
        assert active_count() == baseline

    async def test_exception_is_logged(self, caplog):
        import logging
        caplog.set_level(logging.ERROR, logger="aura.shared.tasks")

        async def boom():
            raise RuntimeError("intentional")

        task = fire_and_forget(boom(), name="boom-task")
        # Wait for completion; the task will raise, but the done callback
        # logs and swallows so awaiting the task surfaces the exception.
        with pytest.raises(RuntimeError, match="intentional"):
            await task
        await asyncio.sleep(0)
        # Verify the logger recorded the exception
        assert any(
            "background task" in rec.message and "boom-task" in rec.message
            for rec in caplog.records
        )

    async def test_cancellation_is_silent(self, caplog):
        import logging
        caplog.set_level(logging.ERROR, logger="aura.shared.tasks")

        async def long_running():
            await asyncio.sleep(10)

        task = fire_and_forget(long_running())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        # Cancellation must not be logged as an error
        assert not any(
            "background task" in rec.message
            for rec in caplog.records
        )
