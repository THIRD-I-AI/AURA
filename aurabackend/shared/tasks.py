"""
Background task helper — strong-reference task tracking.

Python's ``asyncio`` keeps only weak references to tasks created via
``asyncio.create_task``. The docs are explicit:

    The task is held in a weak reference. The event loop only keeps
    weak references to tasks. A task that is not referenced from
    somewhere else may get garbage collected at any time, even before
    it's done.

— https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task

The anti-pattern that triggers this:

    asyncio.create_task(some_coro())   # ← returned task immediately dropped

Under contention the task can be collected mid-flight, producing the
"Task was destroyed but it is pending!" warning in production and
silently dropping the work.

``fire_and_forget`` retains a module-level strong reference until the
task completes, then drops it (so the task itself doesn't leak). It
also logs uncaught exceptions — by default a task whose result is
never awaited swallows its exception.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Coroutine, Optional, Set

logger = logging.getLogger("aura.shared.tasks")

# Module-level strong references. Tasks remove themselves via
# add_done_callback when they finish.
_background_tasks: Set[asyncio.Task] = set()


def fire_and_forget(
    coro: Coroutine,
    *,
    name: Optional[str] = None,
) -> asyncio.Task:
    """Schedule ``coro`` as a tracked background task.

    Equivalent to ``asyncio.create_task`` but holds a strong reference
    until the task completes, so the GC can't reap it mid-execution.
    Logs the task's exception (if any) so silent failures are visible.

    Use this instead of ``asyncio.create_task(coro)`` whenever the
    return value would otherwise be dropped.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error(
            "background task %r raised: %s",
            task.get_name(), exc, exc_info=exc,
        )


def active_count() -> int:
    """Diagnostic: number of background tasks currently in-flight."""
    return len(_background_tasks)


__all__ = ["fire_and_forget", "active_count"]
