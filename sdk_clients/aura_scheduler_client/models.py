"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: 4dd00e39fa97af3b
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class CreateJobRequest(BaseModel):
    connection_id: str
    name: str
    query: str
    schedule_type: "ScheduleType"
    cron_expression: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    max_retries: Optional[int] = None
    retry_delay_seconds: Optional[int] = None
    schedule_config: Optional[Dict[str, Any]] = None
    store_results: Optional[bool] = None
    timeout_seconds: Optional[int] = None


class ExecutionResponse(BaseModel):
    completed_at: Optional[str]
    created_at: str
    duration_seconds: Optional[int]
    error_details: Optional[Dict[str, Any]]
    error_message: Optional[str]
    id: str
    job_id: str
    result_summary: Optional[Dict[str, Any]]
    retry_count: int
    rows_affected: Optional[int]
    started_at: Optional[str]
    status: str
    triggered_by: str


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class JobResponse(BaseModel):
    connection_id: str
    created_at: str
    cron_expression: Optional[str]
    description: Optional[str]
    id: str
    is_active: bool
    last_execution_time: Optional[str]
    max_retries: int
    name: str
    next_execution_time: Optional[str]
    query: str
    retry_delay_seconds: int
    schedule_config: Optional[Dict[str, Any]]
    schedule_type: str
    store_results: bool
    timeout_seconds: int
    updated_at: str


class JobStatus(BaseModel):
    """Job execution status"""
    pass


class LogEntry(BaseModel):
    details: Optional[Dict[str, Any]]
    execution_id: str
    id: str
    level: str
    message: str
    timestamp: str


class ScheduleType(BaseModel):
    """Schedule frequency types"""
    pass


class UpdateJobRequest(BaseModel):
    cron_expression: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    max_retries: Optional[int] = None
    name: Optional[str] = None
    query: Optional[str] = None
    retry_delay_seconds: Optional[int] = None
    schedule_config: Optional[Dict[str, Any]] = None
    schedule_type: Optional["ScheduleType"] = None
    store_results: Optional[bool] = None
    timeout_seconds: Optional[int] = None


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str

