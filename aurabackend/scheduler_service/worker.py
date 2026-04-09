"""
Background worker — executes scheduled jobs with dependency-aware ordering.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from .models import JobStatus, ScheduledJob
from .repository import SchedulerRepository
from .executor import JobExecutor

logger = logging.getLogger(__name__)


class SchedulerWorker:
    """
    Background worker for executing scheduled jobs.

    Supports job dependency DAGs: a job whose `depends_on` list contains
    other job IDs will only run after all upstream jobs have completed
    successfully in the current scheduling tick.
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

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        if self.running:
            logger.warning("Worker already running")
            return
        self.running = True
        self._task = asyncio.create_task(self._worker_loop(), name="scheduler-worker")
        logger.info("Scheduler worker started (interval=%ds)", self.check_interval)

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler worker stopped")

    # ── Main loop ──────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        while self.running:
            try:
                await self._check_and_execute_jobs()
            except Exception as exc:
                logger.error("Worker loop error: %s", exc, exc_info=True)
            await asyncio.sleep(self.check_interval)

    async def _check_and_execute_jobs(self) -> None:
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
