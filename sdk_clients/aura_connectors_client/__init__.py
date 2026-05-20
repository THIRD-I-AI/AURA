"""
Public surface of the auto-generated SDK package ``aura_connectors_client``.

Re-exports every Pydantic model from ``models`` plus the typed
``Client`` + exception classes from ``client``. Regenerate via
``scripts/generate_sdk.py`` — see that module for the CLI; never
edit this file by hand.
"""
from __future__ import annotations

from .client import (
    APIError,
    AsyncClient,
    Client,
    NotFoundError,
    RetryPolicy,
    ServiceUnavailableError,
    UnauthorizedError,
)
from .models import (
    ConnectorTestRequest,
    ConnectorTestResponse,
    HTTPValidationError,
    IngestRequest,
    IntrospectRequest,
    QueryRequest,
    SpatialQueryRequest,
    TableListRequest,
    TableListResponse,
    ValidationError,
    VaultQueryRequest,
    VectorSearchRequest,
)

__all__ = [
    "APIError",
    "AsyncClient",
    "Client",
    "NotFoundError",
    "RetryPolicy",
    "ServiceUnavailableError",
    "UnauthorizedError",
    "ConnectorTestRequest",
    "ConnectorTestResponse",
    "HTTPValidationError",
    "IngestRequest",
    "IntrospectRequest",
    "QueryRequest",
    "SpatialQueryRequest",
    "TableListRequest",
    "TableListResponse",
    "ValidationError",
    "VaultQueryRequest",
    "VectorSearchRequest",
]
