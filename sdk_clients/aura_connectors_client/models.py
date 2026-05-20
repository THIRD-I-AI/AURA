"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: 576d4b03358ffe25
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class ConnectorTestRequest(BaseModel):
    """Request to test a connector"""
    config: Dict[str, Any]
    connector_type: str


class ConnectorTestResponse(BaseModel):
    """Response from connector test"""
    message: str
    success: bool
    error: Optional[str] = None
    table_count: Optional[int] = None


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class IngestRequest(BaseModel):
    """Request to ingest a file and profile it"""
    file_path: str


class IntrospectRequest(BaseModel):
    """Request to introspect a database schema"""
    connector_type: str
    config: Optional[Dict[str, Any]] = None


class QueryRequest(BaseModel):
    """Request to execute a SQL query"""
    query: str
    connection_id: Optional[str] = None
    limit: Optional[int] = None


class SpatialQueryRequest(BaseModel):
    query: str
    params: Optional[List[Any]] = None


class TableListRequest(BaseModel):
    """Request to list tables from a connector"""
    config: Dict[str, Any]
    connector_type: str


class TableListResponse(BaseModel):
    """List of tables from a connector"""
    connector_id: str
    tables: List[str]
    total_count: int


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str


class VaultQueryRequest(BaseModel):
    query: str
    limit: Optional[int] = None


class VectorSearchRequest(BaseModel):
    embedding: List[float]
    column: Optional[str] = None
    limit: Optional[int] = None
    metric: Optional[str] = None
    table: Optional[str] = None

