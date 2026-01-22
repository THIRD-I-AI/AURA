"""
Scheduler Service - FastAPI REST API for managing scheduled jobs
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

from scheduler_service.models import JobStatus, ScheduleType
from scheduler_service.repository import SchedulerRepository
from scheduler_service.executor import JobExecutor
from scheduler_service.worker import SchedulerWorker


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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


# Initialize FastAPI app
app = FastAPI(
    title="AURA Scheduler Service",
    description="Automated job scheduling and execution service",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
repository: Optional[SchedulerRepository] = None
executor: Optional[JobExecutor] = None
worker: Optional[SchedulerWorker] = None


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    global repository, executor, worker
    
    logger.info("Starting Scheduler Service...")
    
    # Get database URL from environment or use default
    db_url = os.getenv(
        "SCHEDULER_DATABASE_URL",
        "sqlite+aiosqlite:///data/scheduler.db"
    )
    
    # Initialize repository
    repository = SchedulerRepository(db_url)
    await repository.init_db()
    logger.info(f"Initialized database: {db_url}")
    
    # Initialize executor
    database_service_url = os.getenv(
        "DATABASE_SERVICE_URL",
        "http://localhost:8002"
    )
    executor = JobExecutor(repository, database_service_url)
    logger.info(f"Initialized executor (database service: {database_service_url})")
    
    # Initialize and start worker
    check_interval = int(os.getenv("SCHEDULER_CHECK_INTERVAL", "60"))
    worker = SchedulerWorker(repository, executor, check_interval)
    await worker.start()
    
    logger.info("Scheduler Service started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global worker, executor
    
    logger.info("Shutting down Scheduler Service...")
    
    if worker:
        await worker.stop()
    
    if executor:
        await executor.close()
    
    logger.info("Scheduler Service shutdown complete")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "scheduler",
        "status": "running",
        "worker_active": worker.running if worker else False
    }


# ==================== Job Management Endpoints ====================

@app.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
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


@app.get("/jobs", response_model=List[JobResponse])
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


@app.get("/jobs/{job_id}", response_model=JobResponse)
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


@app.put("/jobs/{job_id}", response_model=JobResponse)
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


@app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
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


@app.post("/jobs/{job_id}/pause", response_model=JobResponse)
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


@app.post("/jobs/{job_id}/resume", response_model=JobResponse)
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


@app.post("/jobs/{job_id}/execute", response_model=ExecutionResponse)
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

@app.get("/executions", response_model=List[ExecutionResponse])
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


@app.get("/executions/{execution_id}", response_model=ExecutionResponse)
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


@app.get("/executions/{execution_id}/logs", response_model=List[LogEntry])
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

@app.post("/jobs/{job_id}/run")
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

@app.post("/admin/cleanup")
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
    uvicorn.run(app, host="0.0.0.0", port=port)
