"""
AURA Data Connectors Module
Multi-source data connectivity for profiling and semantic modeling
"""

from .base import BaseConnector, ConnectorConfig, ConnectorMetadata, SourceType
from .postgresql_connector import PostgreSQLConnector
from .mysql_connector import MySQLConnector
from .bigquery_connector import BigQueryConnector
from .duckdb_connector import DuckDBConnector

__all__ = [
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorMetadata",
    "SourceType",
    "PostgreSQLConnector",
    "MySQLConnector",
    "BigQueryConnector",
    "DuckDBConnector",
]
