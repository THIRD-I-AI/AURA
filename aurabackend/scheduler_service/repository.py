"""
Database connection and repository for scheduler service
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from .models import Base, ExecutionLog, JobExecution, JobStatus, ScheduledJob, ScheduleType


class SchedulerRepository:
    """Data access layer for scheduler service"""

    def __init__(self, database_url: Optional[str] = None):
        """Initialize repository with database connection"""
        if database_url is None:
            # Default to SQLite for scheduler metadata
            db_path = os.path.join(os.path.dirname(__file__), "..", "data", "scheduler.db")
            database_url = f"sqlite+aiosqlite:///{db_path}"

        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=False,
            future=True
        )

        self.async_session = sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    async def init_db(self):
        """Create all tables"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # ===== Scheduled Jobs =====

    async def create_job(self, job_data: Dict[str, Any]) -> ScheduledJob:
        """Create a new scheduled job"""
        if "id" not in job_data:
            job_data["id"] = str(uuid.uuid4())

        async with self.async_session() as session:
            job = ScheduledJob(**job_data)
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    async def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        """Get job by ID"""
        async with self.async_session() as session:
            result = await session.execute(
                select(ScheduledJob).where(ScheduledJob.id == job_id)
            )
            return result.scalar_one_or_none()

    async def list_jobs(
        self,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[ScheduledJob]:
        """List all scheduled jobs"""
        async with self.async_session() as session:
            query = select(ScheduledJob)

            if is_active is not None:
                query = query.where(ScheduledJob.is_active == is_active)

            query = query.order_by(ScheduledJob.created_at.desc())
            query = query.limit(limit).offset(offset)

            result = await session.execute(query)
            return list(result.scalars().all())

    async def update_job(self, job_id: str, updates: Dict[str, Any]) -> Optional[ScheduledJob]:
        """Update job configuration"""
        async with self.async_session() as session:
            updates["updated_at"] = datetime.now(timezone.utc)

            await session.execute(
                update(ScheduledJob)
                .where(ScheduledJob.id == job_id)
                .values(**updates)
            )
            await session.commit()

            result = await session.execute(
                select(ScheduledJob).where(ScheduledJob.id == job_id)
            )
            return result.scalar_one_or_none()

    async def delete_job(self, job_id: str) -> bool:
        """Delete a scheduled job"""
        async with self.async_session() as session:
            result = await session.execute(
                delete(ScheduledJob).where(ScheduledJob.id == job_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def get_jobs_to_execute(self, current_time: datetime) -> List[ScheduledJob]:
        """Get jobs that should be executed now"""
        async with self.async_session() as session:
            query = select(ScheduledJob).where(
                ScheduledJob.is_active == True,
                ScheduledJob.next_execution_time <= current_time
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    # ===== Job Executions =====

    async def create_execution(self, execution_data: Dict[str, Any]) -> JobExecution:
        """Create a new job execution record"""
        if "id" not in execution_data:
            execution_data["id"] = str(uuid.uuid4())

        async with self.async_session() as session:
            execution = JobExecution(**execution_data)
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            return execution

    async def update_execution(
        self,
        execution_id: str,
        updates: Dict[str, Any]
    ) -> Optional[JobExecution]:
        """Update execution record"""
        async with self.async_session() as session:
            await session.execute(
                update(JobExecution)
                .where(JobExecution.id == execution_id)
                .values(**updates)
            )
            await session.commit()

            result = await session.execute(
                select(JobExecution).where(JobExecution.id == execution_id)
            )
            return result.scalar_one_or_none()

    async def get_execution(self, execution_id: str) -> Optional[JobExecution]:
        """Get execution by ID"""
        async with self.async_session() as session:
            result = await session.execute(
                select(JobExecution).where(JobExecution.id == execution_id)
            )
            return result.scalar_one_or_none()

    async def list_executions(
        self,
        job_id: Optional[str] = None,
        status: Optional[JobStatus] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[JobExecution]:
        """List job executions"""
        async with self.async_session() as session:
            query = select(JobExecution)

            if job_id:
                query = query.where(JobExecution.job_id == job_id)
            if status:
                query = query.where(JobExecution.status == status)

            query = query.order_by(JobExecution.created_at.desc())
            query = query.limit(limit).offset(offset)

            result = await session.execute(query)
            return list(result.scalars().all())

    # ===== Execution Logs =====

    async def add_log(self, log_data: Dict[str, Any]) -> ExecutionLog:
        """Add a log entry"""
        if "id" not in log_data:
            log_data["id"] = str(uuid.uuid4())

        async with self.async_session() as session:
            log = ExecutionLog(**log_data)
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    async def get_logs(
        self,
        execution_id: str,
        level: Optional[str] = None,
        limit: int = 1000
    ) -> List[ExecutionLog]:
        """Get logs for an execution"""
        async with self.async_session() as session:
            query = select(ExecutionLog).where(
                ExecutionLog.execution_id == execution_id
            )

            if level:
                query = query.where(ExecutionLog.level == level)

            query = query.order_by(ExecutionLog.timestamp.asc())
            query = query.limit(limit)

            result = await session.execute(query)
            return list(result.scalars().all())

    # ===== Cleanup =====

    async def cleanup_old_executions(self, retention_days: int = 30) -> int:
        """Delete old execution records"""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

        async with self.async_session() as session:
            # Delete old logs
            await session.execute(
                delete(ExecutionLog).where(
                    ExecutionLog.timestamp < cutoff_date
                )
            )

            # Delete old executions
            result = await session.execute(
                delete(JobExecution).where(
                    JobExecution.created_at < cutoff_date
                )
            )
            await session.commit()
            return result.rowcount
