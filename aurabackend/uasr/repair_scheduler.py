"""UASR Repair Scheduler — global multi-pipeline recovery admission control.

Each MAPE-K worker owns one source. When drift fires, the worker runs an
expensive recovery (LLM shim synthesis + sandbox validation + canary). With N
pipelines drifting at once, N recoveries hit the *same* shared backend (the LLM
API, the sandbox pool) concurrently — there is no bound on concurrency and no
ordering, so a CRITICAL pipeline's repair can wait behind dozens of trivial
ones and the backend saturates.

``RepairScheduler`` is a process-global admission gate. Workers submit repair
coroutines; the scheduler runs at most ``max_concurrent`` at a time, admits them
in priority order (severity, then FIFO within a severity for fairness), and
records per-severity wait/run statistics. It is intentionally small and
dependency-free (pure asyncio) so it can front any recovery callable.

Design properties (all covered by tests):
  * **Bounded concurrency** — never more than ``max_concurrent`` repairs in
    flight, so the shared backend sees a fixed maximum load regardless of N.
  * **Priority ordering** — CRITICAL > HIGH > MEDIUM > LOW; a critical repair
    submitted after a backlog of low-severity repairs is admitted next.
  * **FIFO fairness within a severity** — no starvation among equals.
  * **Backpressure visibility** — queue depth and per-severity wait times are
    observable for the Hᵤ/observability layer.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .models import DriftSeverity

# Severity → numeric rank (higher = more urgent → admitted first).
_SEVERITY_RANK: Dict[DriftSeverity, int] = {
    DriftSeverity.CRITICAL: 3,
    DriftSeverity.HIGH: 2,
    DriftSeverity.MEDIUM: 1,
    DriftSeverity.LOW: 0,
}


@dataclass(order=True)
class _QueueItem:
    # heapq is a min-heap; we sort by (-rank, seq) so higher rank and lower
    # sequence (earlier arrival) come out first.
    sort_key: tuple
    seq: int = field(compare=False)
    source_id: str = field(compare=False)
    severity: DriftSeverity = field(compare=False)
    coro_factory: Callable[[], Awaitable[Any]] = field(compare=False)
    future: asyncio.Future = field(compare=False)
    enqueued_at: float = field(compare=False)


@dataclass
class SchedulerStats:
    submitted: int = 0
    completed: int = 0
    failed: int = 0
    max_observed_concurrency: int = 0
    per_severity_admitted: Dict[str, int] = field(default_factory=dict)
    per_severity_wait_ms: Dict[str, List[float]] = field(default_factory=dict)

    def wait_summary(self) -> Dict[str, float]:
        """Mean admission wait (ms) per severity."""
        return {
            sev: round(sum(v) / len(v), 3) if v else 0.0
            for sev, v in self.per_severity_wait_ms.items()
        }


class RepairScheduler:
    """Global priority-ordered, bounded-concurrency scheduler for repairs.

    Usage::

        scheduler = RepairScheduler(max_concurrent=4)
        await scheduler.start()
        result = await scheduler.submit(source_id, severity, coro_factory)
        await scheduler.stop()

    ``submit`` returns an awaitable that resolves to the repair's result (or
    raises the repair's exception), so a worker can ``await`` its own repair as
    if it had called it directly — the scheduler only controls *when* it runs.
    """

    def __init__(self, max_concurrent: int = 4) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._max_concurrent = max_concurrent
        self._heap: List[_QueueItem] = []
        self._counter = itertools.count()
        self._active = 0
        self._wakeup = asyncio.Event()
        self._stop = asyncio.Event()
        self._pump_task: Optional[asyncio.Task] = None
        self._inflight: set = set()
        self.stats = SchedulerStats()

    async def start(self) -> None:
        if self._pump_task is None:
            self._stop.clear()
            self._pump_task = asyncio.create_task(self._pump(), name="uasr-repair-scheduler")

    async def stop(self, drain: bool = True) -> None:
        """Stop the scheduler. If ``drain``, wait for in-flight repairs first."""
        self._stop.set()
        self._wakeup.set()
        if self._pump_task is not None:
            if drain:
                while self._active > 0 or self._heap:
                    await asyncio.sleep(0.005)
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None

    def queue_depth(self) -> int:
        return len(self._heap)

    def active_count(self) -> int:
        return self._active

    async def submit(
        self,
        source_id: str,
        severity: DriftSeverity,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Enqueue a repair and await its eventual result."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        seq = next(self._counter)
        rank = _SEVERITY_RANK.get(severity, 0)
        item = _QueueItem(
            sort_key=(-rank, seq),
            seq=seq,
            source_id=source_id,
            severity=severity,
            coro_factory=coro_factory,
            future=fut,
            enqueued_at=time.perf_counter(),
        )
        heapq.heappush(self._heap, item)
        self.stats.submitted += 1
        self._wakeup.set()
        return await fut

    async def _pump(self) -> None:
        """Admit queued repairs up to the concurrency bound, in priority order."""
        try:
            while not self._stop.is_set() or self._heap or self._active > 0:
                # Admit as many as capacity allows.
                while self._heap and self._active < self._max_concurrent:
                    item = heapq.heappop(self._heap)
                    self._active += 1
                    self.stats.max_observed_concurrency = max(
                        self.stats.max_observed_concurrency, self._active
                    )
                    wait_ms = (time.perf_counter() - item.enqueued_at) * 1000.0
                    sev = item.severity.value
                    self.stats.per_severity_admitted[sev] = (
                        self.stats.per_severity_admitted.get(sev, 0) + 1
                    )
                    self.stats.per_severity_wait_ms.setdefault(sev, []).append(wait_ms)
                    task = asyncio.create_task(self._run_item(item))
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight.discard)

                if self._stop.is_set() and not self._heap and self._active == 0:
                    break
                # Sleep until something changes (new submit or a repair finishes).
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def _run_item(self, item: _QueueItem) -> None:
        try:
            result = await item.coro_factory()
            if not item.future.done():
                item.future.set_result(result)
            self.stats.completed += 1
        except Exception as exc:  # propagate to the submitter
            if not item.future.done():
                item.future.set_exception(exc)
            self.stats.failed += 1
        finally:
            self._active -= 1
            self._wakeup.set()
