"""
Background worker that checks for scheduled jobs and executes them
"""

import asyncio
from datetime import datetime
import logging

from .repository import SchedulerRepository
from .executor import JobExecutor


logger = logging.getLogger(__name__)


class SchedulerWorker:
    """Background worker for executing scheduled jobs"""
    
    def __init__(
        self,
        repository: SchedulerRepository,
        executor: JobExecutor,
        check_interval_seconds: int = 60
    ):
        self.repository = repository
        self.executor = executor
        self.check_interval = check_interval_seconds
        self.running = False
        self._task = None
    
    async def start(self):
        """Start the worker"""
        if self.running:
            logger.warning("Worker already running")
            return
        
        self.running = True
        self._task = asyncio.create_task(self._worker_loop())
        logger.info(f"Scheduler worker started (check interval: {self.check_interval}s)")
    
    async def stop(self):
        """Stop the worker"""
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
    
    async def _worker_loop(self):
        """Main worker loop"""
        while self.running:
            try:
                await self._check_and_execute_jobs()
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
            
            # Wait before next check
            await asyncio.sleep(self.check_interval)
    
    async def _check_and_execute_jobs(self):
        """Check for jobs that need execution and execute them"""
        now = datetime.utcnow()
        
        # Get jobs that are due for execution
        jobs = await self.repository.get_jobs_to_execute(now)
        
        if jobs:
            logger.info(f"Found {len(jobs)} job(s) to execute")
        
        # Execute jobs concurrently (with a limit)
        if jobs:
            # Execute up to 5 jobs concurrently
            semaphore = asyncio.Semaphore(5)
            
            async def execute_with_semaphore(job):
                async with semaphore:
                    try:
                        logger.info(f"Executing job: {job.name} (ID: {job.id})")
                        await self.executor.execute_job(job)
                    except Exception as e:
                        logger.error(f"Failed to execute job {job.id}: {e}", exc_info=True)
            
            await asyncio.gather(
                *[execute_with_semaphore(job) for job in jobs],
                return_exceptions=True
            )
