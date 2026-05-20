"""
Background worker — executes scheduled jobs with dependency-aware ordering.

Sprint S20.2 integration
------------------------
Wires the S20b distributed-queue primitives (DistributedQueue +
AdvisoryLockHolder + compute_lock_id) into the live polling loop:

* **Cron-evaluator leader election** — when multiple worker replicas
  run against the same Postgres, `pg_advisory_lock` ensures only ONE
  pulls + executes the due-jobs set per tick. The others see "not
  acquired" and skip. Eliminates the double-fire risk that previously
  blocked `scheduler.replicas > 1`.

* **LISTEN/NOTIFY wake** — instead of always sleeping the full
  `check_interval` between ticks, the worker also listens on a
  Postgres NOTIFY channel and wakes immediately when a new job is
  inserted. Reduces p99 job latency from 60s (polling interval) to
  sub-second (notify hop).

Auto-detection
--------------
Distributed mode auto-enables when the repository's engine URL is
Postgres (driver starts with ``postgresql``). On SQLite (dev
default) the worker falls back to the pure polling loop — SQLite
has neither LISTEN/NOTIFY nor advisory locks. No config flag
required; the URL tells us everything.

Backward compatibility
----------------------
The constructor signature is unchanged in its existing keyword args.
``check_interval_seconds`` still bounds the maximum sleep between
ticks (defensive fallback even with notifications). ``stop()`` /
``start()`` semantics unchanged. New behaviour is purely additive;
SQLite-backed dev environments behave EXACTLY the same as before.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from .distributed_queue import (
    AdvisoryLockHolder,
    DistributedQueue,
    NotifyPayload,
    compute_lock_id,
)
from .executor import JobExecutor
from .models import ScheduledJob
from .repository import SchedulerRepository

logger = logging.getLogger(__name__)


# Channel name used for `pg_notify('scheduler_jobs', ...)`. Subscribed
# by every worker instance running against the same Postgres. The
# channel is intentionally generic ("scheduler_jobs") rather than
# per-tenant — auto-routing fan-out is wide-broadcast at the DB level
# and filtered downstream.
SCHEDULER_NOTIFY_CHANNEL = "scheduler_jobs"

# Stable name → bigint key for the cron-evaluator advisory lock.
# Computed once at import time so every worker on every replica
# sees the same key (per compute_lock_id's deterministic SHA-256
# derivation). Two workers contend on this key per tick; whichever
# acquires first runs the evaluation, the others skip.
CRON_EVALUATOR_LOCK_ID = compute_lock_id("scheduler_cron_evaluator")


class SchedulerWorker:
    """
    Background worker for executing scheduled jobs.

    Supports job dependency DAGs: a job whose `depends_on` list contains
    other job IDs will only run after all upstream jobs have completed
    successfully in the current scheduling tick.

    Sprint S20.2 — on a Postgres-backed repository, automatically:
        * holds a `pg_advisory_lock` per evaluation tick (leader
          election across replicas);
        * subscribes to `pg_notify('scheduler_jobs', ...)` so a fresh
          insert wakes the worker faster than the polling interval.
    Falls back to pure polling on SQLite.
    """

    def __init__(
        self,
        repository: SchedulerRepository,
        executor: JobExecutor,
        check_interval_seconds: int = 60,
        max_concurrent_jobs: int = 5,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.check_interval = check_interval_seconds
        self.max_concurrent = max_concurrent_jobs
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._wake_event: asyncio.Event = asyncio.Event()
        # Auto-detect distributed mode from the engine URL. Postgres
        # gets LISTEN/NOTIFY + advisory locks; SQLite stays in pure
        # polling. The drivername is "postgresql+asyncpg" or
        # "postgresql+psycopg" depending on driver — we check the
        # prefix.
        self._distributed: bool = repository.engine.url.drivername.startswith(
            "postgresql"
        )
        if self._distributed:
            self._queue: Optional[DistributedQueue] = DistributedQueue(
                repository.engine,
            )
        else:
            self._queue = None

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        if self.running:
            logger.warning("Worker already running")
            return
        self.running = True
        self._task = asyncio.create_task(self._worker_loop(), name="scheduler-worker")
        if self._distributed and self._queue is not None:
            self._listen_task = asyncio.create_task(
                self._listen_loop(), name="scheduler-listen",
            )
        logger.info(
            "Scheduler worker started (interval=%ds, distributed=%s)",
            self.check_interval, self._distributed,
        )

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        # Cancel the listen loop first so it doesn't fire a wake on
        # an already-stopping worker.
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Scheduler worker stopped")

    # ── Main loop ──────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        """Tick on either NOTIFY wake (sub-second) or interval timeout.

        Pre-S20.2 was a hard `asyncio.sleep(check_interval)` between
        ticks → p99 latency = check_interval. With the notify wake
        the typical latency is single-digit milliseconds; the interval
        is now a defensive fallback if the notify hop is missed
        (e.g., dropped network packet, broken LISTEN connection)."""
        while self.running:
            try:
                await self._check_and_execute_jobs()
            except Exception as exc:
                logger.error("Worker loop error: %s", exc, exc_info=True)
            # Sleep until either a wake event OR the interval elapses,
            # whichever comes first. asyncio.wait_for re-raises
            # TimeoutError when the interval expires; we swallow it
            # and continue (the tick was just an interval-driven one,
            # not notify-driven).
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self.check_interval,
                )
                # Consume the wake; reset for next loop iteration.
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass

    async def _listen_loop(self) -> None:
        """Subscribe to the scheduler NOTIFY channel and set the wake
        event on every received event.

        Each notification means "something changed in the jobs table —
        consider running an evaluation tick now". Multiple notify
        events between ticks coalesce into a single wake (asyncio.Event
        is idempotent), so a burst of inserts doesn't pile up
        evaluations."""
        assert self._queue is not None
        try:
            async for _event in self._queue.listen(SCHEDULER_NOTIFY_CHANNEL):
                if not self.running:
                    break
                self._wake_event.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Defensive: if the LISTEN connection dies, log loudly but
            # don't crash the worker — the interval-driven fallback
            # still wakes ticks every check_interval seconds. A real
            # production setup would restart the listen loop on
            # connection loss; defer that to S20.2.1 if it becomes a
            # pain point.
            logger.error(
                "Scheduler LISTEN loop failed: %s — falling back to interval-only ticks",
                exc, exc_info=True,
            )

    async def _check_and_execute_jobs(self) -> None:
        # Sprint S20.2: when running distributed, acquire the cron-
        # evaluator advisory lock first. Non-blocking (timeout_s=0):
        # if another replica holds it, this tick is theirs and we
        # silently skip. The lock auto-releases on connection close
        # so a crashed leader doesn't deadlock the cluster.
        if self._distributed:
            async with AdvisoryLockHolder(
                self.repository.engine,
                lock_id=CRON_EVALUATOR_LOCK_ID,
                timeout_s=0,
            ) as acquired:
                if not acquired:
                    logger.debug(
                        "scheduler cron-evaluator lock not acquired this tick — "
                        "another replica is the leader",
                    )
                    return
                await self._evaluate_and_execute()
        else:
            await self._evaluate_and_execute()

    async def _evaluate_and_execute(self) -> None:
        """The original tick body — pull due jobs, build waves, execute.

        Split out from `_check_and_execute_jobs` so the advisory-lock
        wrapper above is a clean decoration. Unchanged semantics from
        pre-S20.2."""
        now = datetime.now(timezone.utc)
        jobs = await self.repository.get_jobs_to_execute(now)
        if not jobs:
            return

        logger.info("Found %d job(s) to execute", len(jobs))

        # Build dependency-ordered execution waves
        waves = self._resolve_execution_order(jobs)

        semaphore = asyncio.Semaphore(self.max_concurrent)

        for wave_num, wave in enumerate(waves, 1):
            if not wave:
                continue
            logger.info("Executing wave %d/%d: %d job(s)", wave_num, len(waves), len(wave))

            async def _run(job: ScheduledJob) -> None:
                async with semaphore:
                    try:
                        logger.info("Starting job: %s (%s)", job.name, job.id)
                        await self.executor.execute_job(job)
                    except Exception as exc:
                        logger.error("Job %s failed: %s", job.id, exc, exc_info=True)

            await asyncio.gather(*[_run(j) for j in wave], return_exceptions=True)

    # ── Dependency resolution ──────────────────────────────────────

    @staticmethod
    def _resolve_execution_order(jobs: List[ScheduledJob]) -> List[List[ScheduledJob]]:
        """
        Topological sort of jobs by their `depends_on` lists.

        Returns a list of "waves", where each wave can be executed in
        parallel.  Jobs with no dependencies form the first wave.
        Jobs that depend on jobs NOT in this tick's batch are also placed
        in the first wave (their upstream already completed previously).
        """
        job_map: Dict[str, ScheduledJob] = {j.id: j for j in jobs}
        this_tick_ids: Set[str] = set(job_map.keys())

        # Build adjacency: id → set of in-tick dependencies
        deps: Dict[str, Set[str]] = {}
        for job in jobs:
            upstream = set(job.depends_on or []) & this_tick_ids
            deps[job.id] = upstream

        waves: List[List[ScheduledJob]] = []
        remaining = set(job_map.keys())

        while remaining:
            # Jobs whose in-tick dependencies are all satisfied
            ready = {jid for jid in remaining if not deps[jid]}

            if not ready:
                # Cycle detected — run remaining jobs anyway to avoid deadlock
                logger.warning(
                    "Dependency cycle detected in scheduler jobs: %s — running anyway",
                    remaining,
                )
                ready = remaining.copy()

            waves.append([job_map[jid] for jid in sorted(ready)])
            remaining -= ready

            # Remove satisfied deps from the next set
            for jid in remaining:
                deps[jid] -= ready

        return waves


async def notify_jobs_changed(
    repository: SchedulerRepository,
    *,
    kind: str = "job_changed",
    job_id: str = "",
) -> None:
    """Emit a NOTIFY on the scheduler channel.

    Producer-side helper called from the API gateway (or wherever
    jobs are created / activated / updated) so worker replicas wake
    immediately instead of waiting for the next polling tick.

    No-op when the repository is SQLite (no LISTEN/NOTIFY); the
    polling loop is still the safety net there.
    """
    if not repository.engine.url.drivername.startswith("postgresql"):
        return
    try:
        queue = DistributedQueue(repository.engine)
        await queue.notify(
            SCHEDULER_NOTIFY_CHANNEL,
            NotifyPayload(kind=kind, job_id=job_id),
        )
    except Exception as exc:
        # Best-effort: a notify failure should NEVER break the
        # primary create-job / update-job flow. The polling fallback
        # will catch it within check_interval seconds.
        logger.warning("scheduler notify failed (non-fatal): %s", exc)
