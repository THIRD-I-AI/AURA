"""
AURA Data Connectors Module
Multi-source data connectivity for profiling and semantic modeling
"""

from .base import BaseConnector, ConnectorConfig, ConnectorMetadata, SourceType
from .duckdb_connector import DuckDBConnector

# Optional connectors — only available if their driver is installed
try:
    from .postgresql_connector import PostgreSQLConnector
except ImportError:
    PostgreSQLConnector = None  # type: ignore[assignment,misc]

try:
    from .mysql_connector import MySQLConnector
except ImportError:
    MySQLConnector = None  # type: ignore[assignment,misc]

try:
    from .bigquery_connector import BigQueryConnector
except ImportError:
    BigQueryConnector = None  # type: ignore[assignment,misc]

from .registry import (  # noqa: E402  — must be imported after the connector classes above
    ConnectorField,
    ConnectorSpec,
    available_connectors,
    get_connector,
    register_connector,
)

__all__ = [
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorMetadata",
    "SourceType",
    "DuckDBConnector",
    "PostgreSQLConnector",
    "MySQLConnector",
    "BigQueryConnector",
    # Registry
    "ConnectorField",
    "ConnectorSpec",
    "available_connectors",
    "get_connector",
    "register_connector",
]
