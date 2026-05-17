"""
Base connector class for AURA data sources
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional


class SourceType(Enum):
    """Supported data source types"""
    CSV = "csv"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    BIGQUERY = "bigquery"
    SNOWFLAKE = "snowflake"
    MONGODB = "mongodb"
    DUCKDB = "duckdb"
    # Sprint 17 — Multi-Modal Fabric (Pillar 2). FAISS is a first-class
    # vector store separate from pgvector (already accessible through
    # PostgreSQL). DUCKDB_SPATIAL is just DuckDB with the spatial
    # extension auto-loaded; users register it explicitly when they
    # want PostGIS-like queries without a Postgres dependency.
    FAISS = "faiss"
    DUCKDB_SPATIAL = "duckdb_spatial"


@dataclass
class ConnectorConfig:
    """Configuration for data connectors"""
    source_type: SourceType
    name: str
    connection_string: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    credentials_json: Optional[Dict[str, Any]] = None
    extra_params: Optional[Dict[str, Any]] = None


class ConnectorMetadata:
    """Metadata about a connector"""
    def __init__(
        self,
        source_id: str,
        source_type: SourceType,
        display_name: str,
        description: str,
        icon: str,
    ):
        self.source_id = source_id
        self.source_type = source_type
        self.display_name = display_name
        self.description = description
        self.icon = icon
        self.connected = False
        self.last_sync: Optional[str] = None
        self.table_count = 0
        self.row_count_estimate = 0


class BaseConnector(ABC):
    """Abstract base class for all connectors"""

    def __init__(self, config: ConnectorConfig):
        self.config = config
        self.metadata = ConnectorMetadata(
            source_id=config.name,
            source_type=config.source_type,
            display_name=config.name,
            description=f"{config.source_type.value} data source",
            icon=self._get_icon(),
        )
        self._is_connected = False

    def _get_icon(self) -> str:
        """Get icon for source type"""
        icons = {
            SourceType.CSV: "📄",
            SourceType.POSTGRESQL: "🐘",
            SourceType.MYSQL: "🐬",
            SourceType.BIGQUERY: "☁️",
            SourceType.SNOWFLAKE: "❄️",
            SourceType.MONGODB: "🍃",
        }
        return icons.get(self.config.source_type, "📊")

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to data source"""
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """Close connection to data source"""
        pass

    @abstractmethod
    async def list_tables(self) -> List[str]:
        """List all available tables/collections"""
        pass

    @abstractmethod
    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get schema (columns and types) for a table"""
        pass

    @abstractmethod
    async def sample_rows(
        self,
        table_name: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get sample rows from a table"""
        pass

    @abstractmethod
    async def execute_query(self, query: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Execute arbitrary SQL/query and return results"""
        pass

    @abstractmethod
    async def profile_table(self, table_name: str) -> Dict[str, Any]:
        """Generate comprehensive profile for a table"""
        pass

    def is_connected(self) -> bool:
        """Check connection status"""
        return self._is_connected

    async def health_check(self) -> bool:
        """Test connection health"""
        try:
            tables = await self.list_tables()
            return len(tables) >= 0
        except Exception:
            return False

    # ── Sprint 17: Multi-Modal Fabric optional capabilities ─────────
    #
    # The default implementations raise NotImplementedError so a
    # relational connector that doesn't support these operations
    # fails closed rather than silently returning empty results.
    # Connectors that DO support multi-modal ops (FAISS for vectors,
    # DuckDB-spatial for spatial, the existing Postgres adapter for
    # pgvector + PostGIS) override the corresponding method.

    def capabilities(self) -> Dict[str, bool]:
        """Report which multi-modal operations this connector supports.

        Default: ``sql`` only — everything else is opt-in. The service
        layer (``connectors/main.py``) inspects this dict before
        dispatching to ``vector_search()`` or ``spatial_query()`` so a
        request to a non-supporting connector returns 501 rather than
        500.

        Keys follow the same vocabulary as the ConnectorSpec
        ``capabilities`` list in ``registry.py``: ``sql``, ``vector``,
        ``spatial``, ``file_query``, ``time_series``.
        """
        return {"sql": True, "vector": False, "spatial": False}

    async def vector_search(
        self,
        table: str,
        embedding: List[float],
        *,
        column: str = "embedding",
        limit: int = 10,
        metric: str = "cosine",
    ) -> List[Dict[str, Any]]:
        """k-nearest-neighbours over a vector column.

        Default raises ``NotImplementedError``. FAISS / Postgres-pgvector
        / vendored vector connectors override. Returns rows with a
        ``_distance`` (or ``_similarity``) field added; sort order is
        nearest-first.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement vector_search"
        )

    async def spatial_query(
        self,
        query: str,
        params: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Spatial query (PostGIS / DuckDB-spatial dialect).

        Default raises ``NotImplementedError``. DuckDB-spatial and
        Postgres+PostGIS connectors override. The query string is
        passed verbatim — the connector is responsible for any
        parameterisation.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement spatial_query"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation"""
        return {
            "source_id": self.metadata.source_id,
            "source_type": self.config.source_type.value,
            "display_name": self.metadata.display_name,
            "description": self.metadata.description,
            "icon": self.metadata.icon,
            "connected": self.metadata.connected,
            "last_sync": self.metadata.last_sync,
            "table_count": self.metadata.table_count,
        }
