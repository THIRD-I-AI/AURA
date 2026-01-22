"""
Job execution engine for scheduler service
Handles actual query execution, retry logic, and result storage
"""

import asyncio
import httpx
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import traceback
import uuid

from .models import JobStatus, ScheduledJob, JobExecution
from .repository import SchedulerRepository


class JobExecutor:
    """Executes scheduled jobs and handles retries"""
    
    def __init__(
        self,
        repository: SchedulerRepository,
        database_service_url: str = "http://localhost:8002"
    ):
        self.repository = repository
        self.database_service_url = database_service_url
        self.http_client = httpx.AsyncClient(timeout=300.0)
    
    async def execute_job(
        self,
        job: ScheduledJob,
        triggered_by: str = "scheduler"
    ) -> JobExecution:
        """Execute a single job"""
        
        # Create execution record
        execution = await self.repository.create_execution({
            "job_id": job.id,
            "status": JobStatus.PENDING,
            "triggered_by": triggered_by,
            "retry_count": 0
        })
        
        await self._log(execution.id, "INFO", f"Starting execution of job '{job.name}'")
        
        try:
            # Update status to running
            await self.repository.update_execution(
                execution.id,
                {
                    "status": JobStatus.RUNNING,
                    "started_at": datetime.utcnow()
                }
            )
            
            # Execute query via database service
            result = await self._execute_query(
                connection_id=job.connection_id,
                query=job.query,
                timeout=job.timeout_seconds
            )
            
            # Calculate duration
            duration = (datetime.utcnow() - execution.started_at).total_seconds() if execution.started_at else 0
            
            # Update execution with results
            await self.repository.update_execution(
                execution.id,
                {
                    "status": JobStatus.SUCCESS,
                    "completed_at": datetime.utcnow(),
                    "duration_seconds": int(duration),
                    "rows_affected": result.get("row_count", 0),
                    "result_summary": {
                        "columns": result.get("columns", []),
                        "sample_rows": result.get("rows", [])[:5]  # Store first 5 rows
                    }
                }
            )
            
            await self._log(
                execution.id,
                "INFO",
                f"Job completed successfully. Rows: {result.get('row_count', 0)}, Duration: {duration:.2f}s"
            )
            
            # Update job's last execution time
            await self.repository.update_job(
                job.id,
                {
                    "last_execution_time": datetime.utcnow(),
                    "next_execution_time": self._calculate_next_execution(job)
                }
            )
            
        except Exception as e:
            error_message = str(e)
            error_details = {
                "type": type(e).__name__,
                "traceback": traceback.format_exc()
            }
            
            await self._log(
                execution.id,
                "ERROR",
                f"Job execution failed: {error_message}",
                error_details
            )
            
            # Check if we should retry
            execution = await self.repository.get_execution(execution.id)
            if execution and execution.retry_count < job.max_retries:
                # Schedule retry
                retry_count = execution.retry_count + 1
                await self.repository.update_execution(
                    execution.id,
                    {
                        "status": JobStatus.PENDING,
                        "retry_count": retry_count,
                        "error_message": error_message,
                        "error_details": error_details
                    }
                )
                
                await self._log(
                    execution.id,
                    "INFO",
                    f"Scheduling retry {retry_count}/{job.max_retries} in {job.retry_delay_seconds}s"
                )
                
                # Retry after delay
                await asyncio.sleep(job.retry_delay_seconds)
                return await self.execute_job(job, triggered_by)
            else:
                # Max retries exceeded
                duration = (datetime.utcnow() - execution.started_at).total_seconds() if execution.started_at else 0
                
                await self.repository.update_execution(
                    execution.id,
                    {
                        "status": JobStatus.FAILED,
                        "completed_at": datetime.utcnow(),
                        "duration_seconds": int(duration),
                        "error_message": error_message,
                        "error_details": error_details
                    }
                )
                
                # TODO: Send failure notification emails
        
        # Fetch and return final execution state
        return await self.repository.get_execution(execution.id)
    
    async def _execute_query(
        self,
        connection_id: str,
        query: str,
        timeout: int = 300
    ) -> Dict[str, Any]:
        """Execute query via database service"""
        
        url = f"{self.database_service_url}/connections/{connection_id}/query"
        
        try:
            response = await self.http_client.post(
                url,
                json={
                    "connection_id": connection_id,
                    "query": query,
                    "limit": 10000  # Max rows to fetch
                },
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            raise Exception(f"Database service returned error: {e.response.status_code} - {e.response.text}")
        except httpx.TimeoutException:
            raise Exception(f"Query execution timed out after {timeout}s")
        except Exception as e:
            raise Exception(f"Failed to execute query: {str(e)}")
    
    async def _log(
        self,
        execution_id: str,
        level: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Add a log entry"""
        await self.repository.add_log({
            "execution_id": execution_id,
            "level": level,
            "message": message,
            "details": details
        })
    
    def _calculate_next_execution(self, job: ScheduledJob) -> Optional[datetime]:
        """Calculate next execution time based on schedule"""
        now = datetime.utcnow()
        
        if not job.is_active:
            return None
        
        if job.schedule_type == "once":
            return None  # One-time job, no next execution
        
        elif job.schedule_type == "hourly":
            return now + timedelta(hours=1)
        
        elif job.schedule_type == "daily":
            # Execute at the same hour tomorrow
            config = job.schedule_config or {}
            hour = config.get("hour", 0)
            minute = config.get("minute", 0)
            
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            return next_run
        
        elif job.schedule_type == "weekly":
            # Execute on specific day of week
            config = job.schedule_config or {}
            day_of_week = config.get("day_of_week", 0)  # 0=Monday, 6=Sunday
            hour = config.get("hour", 0)
            minute = config.get("minute", 0)
            
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            days_ahead = day_of_week - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_run += timedelta(days=days_ahead)
            return next_run
        
        elif job.schedule_type == "monthly":
            # Execute on specific day of month
            config = job.schedule_config or {}
            day = config.get("day", 1)
            hour = config.get("hour", 0)
            minute = config.get("minute", 0)
            
            next_run = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                # Move to next month
                if next_run.month == 12:
                    next_run = next_run.replace(year=next_run.year + 1, month=1)
                else:
                    next_run = next_run.replace(month=next_run.month + 1)
            return next_run
        
        # For cron expressions, would need a cron parser library
        # For now, default to hourly
        return now + timedelta(hours=1)
    
    async def close(self):
        """Cleanup resources"""
        await self.http_client.aclose()
