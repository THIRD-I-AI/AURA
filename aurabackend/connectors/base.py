"""
Base connector class for AURA data sources
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, AsyncGenerator
import asyncio


class SourceType(Enum):
    """Supported data source types"""
    CSV = "csv"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    BIGQUERY = "bigquery"
    SNOWFLAKE = "snowflake"
    MONGODB = "mongodb"


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
