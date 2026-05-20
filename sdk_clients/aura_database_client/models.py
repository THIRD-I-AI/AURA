"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: bcab8a6b0fb5e408
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class DatabaseConnectionRequest(BaseModel):
    name: str
    type: "DatabaseType"
    connection_string: Optional[str] = None
    database: Optional[str] = None
    host: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    password: Optional[str] = None
    port: Optional[int] = None
    ssl_enabled: Optional[bool] = None
    username: Optional[str] = None


class DatabaseConnectionResponse(BaseModel):
    created_at: str
    database: str
    host: str
    id: str
    is_active: bool
    metadata: Dict[str, Any]
    name: str
    port: int
    ssl_enabled: bool
    type: "DatabaseType"
    updated_at: str
    username: str


class DatabaseType(BaseModel):
    pass


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class QueryRequest(BaseModel):
    connection_id: str
    query: str
    limit: Optional[int] = None


class QueryResponse(BaseModel):
    columns: List[str]
    execution_time_ms: int
    row_count: int
    rows: List[List[Any]]


class SchemaResponse(BaseModel):
    connection_id: str
    functions: List[Dict[str, Any]]
    last_updated: str
    procedures: List[Dict[str, Any]]
    schemas: List[str]
    tables: List[Dict[str, Any]]
    views: List[Dict[str, Any]]


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str
    ctx: Optional[Dict[str, Any]] = None
    input: Optional[Any] = None

