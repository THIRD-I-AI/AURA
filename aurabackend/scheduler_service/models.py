"""
Database models for scheduler service
Stores scheduled jobs, execution history, and logs
"""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.types import TypeDecorator

Base = declarative_base()


class UTCDateTime(TypeDecorator):
    """A naive-UTC TIMESTAMP column that accepts and returns aware UTC datetimes.

    BUG-356: the columns are TIMESTAMP WITHOUT TIME ZONE, but the repository and
    executor write ``datetime.now(timezone.utc)`` (updated_at, started_at,
    completed_at, last/next_execution_time). asyncpg refuses an aware value for a
    naive column, so on the Postgres stack creating, updating, pausing, resuming and
    running jobs all failed; and on SQLite a value read back came out naive, so
    ``now(timezone.utc) - started_at`` raised. Normalising here covers every write
    path; the stored representation (naive UTC) is unchanged, so no migration.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class JobStatus(str, Enum):
    """Job execution status"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScheduleType(str, Enum):
    """Schedule frequency types"""
    ONCE = "once"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CRON = "cron"


class ScheduledJob(Base):
    """Scheduled job configuration"""
    __tablename__ = "scheduled_jobs"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Connection and query
    connection_id = Column(String(36), nullable=False)
    query = Column(Text, nullable=False)

    # Schedule configuration
    schedule_type = Column(SQLEnum(ScheduleType), nullable=False)
    cron_expression = Column(String(100), nullable=True)  # For custom cron schedules
    schedule_config = Column(JSON, nullable=True)  # Additional schedule params (day_of_week, hour, etc.)

    # Execution settings
    timeout_seconds = Column(Integer, default=300)
    max_retries = Column(Integer, default=3)
    retry_delay_seconds = Column(Integer, default=60)

    # Job dependency DAG
    depends_on = Column(JSON, nullable=True)  # List[str] of job IDs that must succeed first
    on_failure_action = Column(String(32), default="abort")  # abort | continue | retry_upstream

    # Output configuration
    store_results = Column(Boolean, default=True)
    result_retention_days = Column(Integer, default=30)
    notification_emails = Column(JSON, nullable=True)  # List of emails for alerts

    # Status
    is_active = Column(Boolean, default=True)
    last_execution_time = Column(UTCDateTime, nullable=True)
    next_execution_time = Column(UTCDateTime, nullable=True)

    # Metadata
    created_by = Column(String(100), nullable=True)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class JobExecution(Base):
    """Individual job execution record"""
    __tablename__ = "job_executions"

    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), nullable=False, index=True)

    # Execution info
    status = Column(SQLEnum(JobStatus), nullable=False, default=JobStatus.PENDING)
    started_at = Column(UTCDateTime, nullable=True)
    completed_at = Column(UTCDateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    # Results
    rows_affected = Column(Integer, nullable=True)
    result_summary = Column(JSON, nullable=True)  # Sample results or stats
    result_location = Column(String(500), nullable=True)  # File path or S3 URL

    # Error tracking
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)
    retry_count = Column(Integer, default=0)

    # Metadata
    triggered_by = Column(String(50), default="scheduler")  # scheduler, manual, api
    execution_metadata = Column(JSON, nullable=True)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class ExecutionLog(Base):
    """Detailed execution logs"""
    __tablename__ = "execution_logs"

    id = Column(String(36), primary_key=True)
    execution_id = Column(String(36), nullable=False, index=True)

    # Log entry
    timestamp = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    level = Column(String(20), nullable=False)  # INFO, WARNING, ERROR
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
