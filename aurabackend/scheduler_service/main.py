"""
Scheduler Service - FastAPI REST API for managing scheduled jobs
"""

import sys
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

# Add parent directory to path for imports

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from shared.service_factory import create_service
from shared.config import settings
from shared.logging_config import get_logger
from scheduler_service.models import JobStatus, ScheduleType
from scheduler_service.repository import SchedulerRepository
from scheduler_service.executor import JobExecutor
from scheduler_service.worker import SchedulerWorker


logger = get_logger("aura.scheduler")

# Mutable state container — avoids global keyword issues with asynccontextmanager
_state: Dict[str, Any] = {"repository": None, "executor": None, "worker": None}


@asynccontextmanager
async def _scheduler_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: init DB, executor, worker.  Shutdown: stop worker."""
    logger.info("Starting Scheduler Service...")

    db_url = settings.scheduler_database_url
    _state["repository"] = SchedulerRepository(db_url)
    await _state["repository"].init_db()
    logger.info("Initialized database: %s", db_url)

    _state["executor"] = JobExecutor(_state["repository"], settings.database_service_url)
    logger.info("Initialized executor (database service: %s)", settings.database_service_url)

    _state["worker"] = SchedulerWorker(
        _state["repository"], _state["executor"], settings.scheduler_check_interval,
    )
    await _state["worker"].start()
    logger.info("Scheduler Service started successfully")

    yield

    logger.info("Shutting down Scheduler Service...")
    if _state["worker"]:
        await _state["worker"].stop()
    if _state["executor"]:
        await _state["executor"].close()
    logger.info("Scheduler Service shutdown complete")


# Convenience helper kept for readability — endpoints use _Proxy aliases below


# Initialize FastAPI app — single app used by orchestrator and all routes
scheduler_app = create_service(
    name="Scheduler",
    service_tag="scheduler",
    description="Automated job scheduling and execution service",
    lifespan=_scheduler_lifespan,
)


# Pydantic models for API requests/responses
class CreateJobRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    connection_id: str = Field(..., description="Database connection ID")
    query: str = Field(..., min_length=1, description="SQL query to execute")
    schedule_type: ScheduleType = Field(..., description="Schedule type")
    schedule_config: Optional[Dict[str, Any]] = Field(
        None,
        description="Schedule configuration (hour, minute, day_of_week, etc.)"
    )
    cron_expression: Optional[str] = Field(
        None,
        description="Cron expression (if schedule_type is 'cron')"
    )
    timeout_seconds: int = Field(300, ge=1, le=3600)
    max_retries: int = Field(3, ge=0, le=10)
    retry_delay_seconds: int = Field(60, ge=1, le=3600)
    store_results: bool = Field(True, description="Whether to store query results")
    is_active: bool = Field(True, description="Whether job is active")


class UpdateJobRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    query: Optional[str] = None
    schedule_type: Optional[ScheduleType] = None
    schedule_config: Optional[Dict[str, Any]] = None
    cron_expression: Optional[str] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=3600)
    max_retries: Optional[int] = Field(None, ge=0, le=10)
    retry_delay_seconds: Optional[int] = Field(None, ge=1, le=3600)
    store_results: Optional[bool] = None
    is_active: Optional[bool] = None


class JobResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    connection_id: str
    query: str
    schedule_type: str
    schedule_config: Optional[Dict[str, Any]]
    cron_expression: Optional[str]
    timeout_seconds: int
    max_retries: int
    retry_delay_seconds: int
    store_results: bool
    is_active: bool
    last_execution_time: Optional[datetime]
    next_execution_time: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ExecutionResponse(BaseModel):
    id: str
    job_id: str
    status: str
    triggered_by: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[int]
    rows_affected: Optional[int]
    result_summary: Optional[Dict[str, Any]]
    error_message: Optional[str]
    error_details: Optional[Dict[str, Any]]
    retry_count: int
    created_at: datetime


class LogEntry(BaseModel):
    id: str
    execution_id: str
    timestamp: datetime
    level: str
    message: str
    details: Optional[Dict[str, Any]]


# (CORS, logging, error handling all provided by create_service)


@scheduler_app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "scheduler",
        "status": "running",
        "worker_active": _state["worker"].running if _state["worker"] else False
    }


# ==================== Job Management Endpoints ====================
# Note: ``repository``, ``executor``, ``worker`` are resolved via _state dict
# set during lifespan startup.  We define module-level aliases that lookup at
# call time so endpoint handler code doesn't need to change.

class _Proxy:
    """Thin proxy that defers to _state[key] at access time."""
    def __init__(self, key: str):
        self._key = key
    def __getattr__(self, name: str):
        obj = _state[self._key]
        if obj is None:
            raise RuntimeError(f"Scheduler {self._key} not initialized yet")
        return getattr(obj, name)

repository = _Proxy("repository")  # type: ignore[assignment]
executor = _Proxy("executor")      # type: ignore[assignment]
worker = _Proxy("worker")          # type: ignore[assignment]


@scheduler_app.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(job: CreateJobRequest):
    """Create a new scheduled job"""
    try:
        # Calculate initial next execution time
        from .executor import JobExecutor as Exec
        exec_temp = Exec(repository)
        
        # Create a temporary job object for calculation
        from .models import ScheduledJob
        temp_job = ScheduledJob(
            name=job.name,
            schedule_type=job.schedule_type,
            schedule_config=job.schedule_config,
            is_active=job.is_active
        )
        next_execution = exec_temp._calculate_next_execution(temp_job)
        
        # Create job
        created_job = await repository.create_job({
            **job.dict(),
            "next_execution_time": next_execution
        })
        
        logger.info(f"Created job: {created_job.name} (ID: {created_job.id})")
        return created_job
        
    except Exception as e:
        logger.error(f"Failed to create job: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create job: {str(e)}"
        )


@scheduler_app.get("/jobs", response_model=List[JobResponse])
async def list_jobs(is_active: Optional[bool] = None):
    """List all scheduled jobs"""
    try:
        jobs = await repository.list_jobs(is_active)
        return jobs
    except Exception as e:
        logger.error(f"Failed to list jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list jobs: {str(e)}"
        )


@scheduler_app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get a specific job by ID"""
    try:
        job = await repository.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        return job
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job: {str(e)}"
        )


@scheduler_app.put("/jobs/{job_id}", response_model=JobResponse)
async def update_job(job_id: str, updates: UpdateJobRequest):
    """Update a scheduled job"""
    try:
        # Check if job exists
        existing_job = await repository.get_job(job_id)
        if not existing_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        # Apply updates
        update_dict = {k: v for k, v in updates.dict().items() if v is not None}
        
        # Recalculate next execution if schedule changed
        if any(k in update_dict for k in ["schedule_type", "schedule_config", "is_active"]):
            from .executor import JobExecutor as Exec
            exec_temp = Exec(repository)
            
            # Merge updates with existing job
            merged_job = existing_job
            for key, value in update_dict.items():
                setattr(merged_job, key, value)
            
            next_execution = exec_temp._calculate_next_execution(merged_job)
            update_dict["next_execution_time"] = next_execution
        
        updated_job = await repository.update_job(job_id, update_dict)
        
        logger.info(f"Updated job: {job_id}")
        return updated_job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update job: {str(e)}"
        )


@scheduler_app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str):
    """Delete a scheduled job"""
    try:
        # Check if job exists
        existing_job = await repository.get_job(job_id)
        if not existing_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        success = await repository.delete_job(job_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete job"
            )
        
        logger.info(f"Deleted job: {job_id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete job: {str(e)}"
        )


@scheduler_app.post("/jobs/{job_id}/pause", response_model=JobResponse)
async def pause_job(job_id: str):
    """Pause a scheduled job"""
    try:
        job = await repository.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        updated_job = await repository.update_job(job_id, {"is_active": False})
        logger.info(f"Paused job: {job_id}")
        return updated_job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to pause job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to pause job: {str(e)}"
        )


@scheduler_app.post("/jobs/{job_id}/resume", response_model=JobResponse)
async def resume_job(job_id: str):
    """Resume a paused job"""
    try:
        job = await repository.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        # Recalculate next execution
        from .executor import JobExecutor as Exec
        exec_temp = Exec(repository)
        next_execution = exec_temp._calculate_next_execution(job)
        
        updated_job = await repository.update_job(
            job_id,
            {"is_active": True, "next_execution_time": next_execution}
        )
        
        logger.info(f"Resumed job: {job_id}")
        return updated_job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resume job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resume job: {str(e)}"
        )


@scheduler_app.post("/jobs/{job_id}/execute", response_model=ExecutionResponse)
async def execute_job_now(job_id: str):
    """Manually trigger job execution"""
    try:
        job = await repository.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        # Execute job
        execution = await executor.execute_job(job, triggered_by="manual")
        
        logger.info(f"Manually executed job: {job_id}")
        return execution
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute job: {str(e)}"
        )


# ==================== Execution History Endpoints ====================

@scheduler_app.get("/executions", response_model=List[ExecutionResponse])
async def list_executions(
    job_id: Optional[str] = None,
    status: Optional[JobStatus] = None,
    limit: int = 100
):
    """List job executions"""
    try:
        executions = await repository.list_executions(job_id, status)
        return executions[:limit]
    except Exception as e:
        logger.error(f"Failed to list executions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list executions: {str(e)}"
        )


@scheduler_app.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(execution_id: str):
    """Get a specific execution by ID"""
    try:
        execution = await repository.get_execution(execution_id)
        if not execution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id} not found"
            )
        return execution
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get execution {execution_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get execution: {str(e)}"
        )


@scheduler_app.get("/executions/{execution_id}/logs", response_model=List[LogEntry])
async def get_execution_logs(
    execution_id: str,
    level: Optional[str] = None
):
    """Get logs for a specific execution"""
    try:
        # Verify execution exists
        execution = await repository.get_execution(execution_id)
        if not execution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id} not found"
            )
        
        logs = await repository.get_logs(execution_id, level)
        return logs
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get logs for execution {execution_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get logs: {str(e)}"
        )


# ==================== Job Execution Endpoints ====================

@scheduler_app.post("/jobs/{job_id}/run")
async def trigger_job_execution(job_id: str):
    """Manually trigger a job execution"""
    try:
        # Verify job exists
        job = await repository.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        # Execute the job asynchronously using the global executor
        import asyncio
        
        # Start execution in background task
        task = asyncio.create_task(executor.execute_job(job, triggered_by="manual"))
        
        logger.info(f"Manually triggered job {job_id}")
        return {
            "message": "Job execution triggered",
            "job_id": job_id,
            "status": "Job will execute asynchronously"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to trigger job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger job: {str(e)}"
        )


# ==================== Admin Endpoints ====================

@scheduler_app.post("/admin/cleanup")
async def cleanup_old_executions(retention_days: int = 30):
    """Clean up old execution records"""
    try:
        deleted_count = await repository.cleanup_old_executions(retention_days)
        logger.info(f"Cleaned up {deleted_count} old execution records")
        return {
            "deleted_count": deleted_count,
            "retention_days": retention_days
        }
    except Exception as e:
        logger.error(f"Failed to cleanup executions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cleanup: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("SCHEDULER_PORT", "8004"))
    uvicorn.run(scheduler_app, host="0.0.0.0", port=port)
