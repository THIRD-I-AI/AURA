"""
Database models for scheduler service
Stores scheduled jobs, execution history, and logs
"""

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, JSON, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from enum import Enum

Base = declarative_base()


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
    
    # Output configuration
    store_results = Column(Boolean, default=True)
    result_retention_days = Column(Integer, default=30)
    notification_emails = Column(JSON, nullable=True)  # List of emails for alerts
    
    # Status
    is_active = Column(Boolean, default=True)
    last_execution_time = Column(DateTime, nullable=True)
    next_execution_time = Column(DateTime, nullable=True)
    
    # Metadata
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class JobExecution(Base):
    """Individual job execution record"""
    __tablename__ = "job_executions"
    
    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), nullable=False, index=True)
    
    # Execution info
    status = Column(SQLEnum(JobStatus), nullable=False, default=JobStatus.PENDING)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
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
    created_at = Column(DateTime, default=datetime.utcnow)


class ExecutionLog(Base):
    """Detailed execution logs"""
    __tablename__ = "execution_logs"
    
    id = Column(String(36), primary_key=True)
    execution_id = Column(String(36), nullable=False, index=True)
    
    # Log entry
    timestamp = Column(DateTime, default=datetime.utcnow)
    level = Column(String(20), nullable=False)  # INFO, WARNING, ERROR
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
