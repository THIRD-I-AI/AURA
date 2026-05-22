"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: dd430601c82dcf04
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class ExecutionJob(BaseModel):
    job_id: str
    sql: str
    approved: Optional[bool] = None
    connection_id: Optional[str] = None
    error: Optional[str] = None
    limit: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class QueryResult(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    chart_spec: Optional[Dict[str, Any]] = None


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str
    ctx: Optional[Dict[str, Any]] = None
    input: Optional[Any] = None

