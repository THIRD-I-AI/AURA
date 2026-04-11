"""
AURA Database Connection Manager
Enterprise-grade database connectivity with schema introspection
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, cast

from sqlalchemy import inspect, text
from sqlalchemy.engine import URL, Connection
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


# Database connection types
class DatabaseType(Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"
    MSSQL = "mssql"
    ORACLE = "oracle"
    MONGODB = "mongodb"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    REDSHIFT = "redshift"
    DATABRICKS = "databricks"
    CLICKHOUSE = "clickhouse"
    CASSANDRA = "cassandra"

@dataclass
class DatabaseConnection:
    id: str
    name: str
    type: DatabaseType
    host: str
    port: int
    database: str
    username: str
    password: str  # Should be encrypted in production
    ssl_enabled: bool = False
    connection_string: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_active: bool = True
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()
        if self.metadata is None:
            self.metadata = {}

@dataclass
class TableSchema:
    name: str
    schema: str
    columns: List[Dict[str, Any]]
    primary_keys: List[str]
    foreign_keys: List[Dict[str, Any]]
    indexes: List[Dict[str, Any]]
    row_count: Optional[int] = None
    table_type: str = "TABLE"
    description: Optional[str] = None

@dataclass
class DatabaseSchema:
    connection_id: str
    schemas: List[str]
    tables: List[TableSchema]
    views: List[TableSchema]
    functions: List[Dict[str, Any]]
    procedures: List[Dict[str, Any]]
    last_updated: Optional[datetime] = None

    def __post_init__(self):
        if self.last_updated is None:
            self.last_updated = datetime.now()

class DatabaseConnectionManager:
    def __init__(self):
        self.connections: Dict[str, DatabaseConnection] = {}
        self.engines: Dict[str, AsyncEngine] = {}
        self.schema_cache: Dict[str, DatabaseSchema] = {}

    def _get_connection_string(self, connection: DatabaseConnection) -> str:
        if connection.connection_string:
            return connection.connection_string
        if connection.type == DatabaseType.SQLITE:
            # SQLite uses only the database path
            db_path = connection.database or "memory"
            return f"sqlite+aiosqlite:///{db_path}"
        if connection.type == DatabaseType.POSTGRESQL:
            query: Dict[str, str] = {}
            if connection.ssl_enabled:
                query["sslmode"] = "require"

            url_kwargs: Dict[str, Any] = {
                "username": connection.username or None,
                "password": connection.password or None,
                "host": connection.host or None,
                "port": connection.port or None,
                "database": connection.database or None,
            }
            if query:
                url_kwargs["query"] = query

            url = URL.create("postgresql+asyncpg", **url_kwargs)
            return str(url)
        if connection.type == DatabaseType.MYSQL:
            # MySQL async driver
            url_kwargs: Dict[str, Any] = {
                "username": connection.username or None,
                "password": connection.password or None,
                "host": connection.host or None,
                "port": connection.port or None,
                "database": connection.database or None,
            }
            url = URL.create("mysql+aiomysql", **url_kwargs)
            return str(url)

        raise NotImplementedError(f"Database type {connection.type.value} not supported yet.")

    def _get_validation_query(self, db_type: DatabaseType) -> str:
        if db_type in {DatabaseType.POSTGRESQL, DatabaseType.SQLITE}:
            return "SELECT 1"
        raise NotImplementedError(f"Validation query not defined for database type {db_type.value}")

    def _create_engine(self, connection: DatabaseConnection) -> AsyncEngine:
        conn_str = self._get_connection_string(connection)
        return create_async_engine(
            conn_str,
            pool_pre_ping=True,
            pool_recycle=1800,
        )

    async def add_connection(self, connection: DatabaseConnection) -> str:
        """Add a new database connection"""
        if not connection.id:
            connection.id = str(uuid.uuid4())

        try:
            is_valid = await self.test_connection(connection)
        except NotImplementedError as exc:
            raise ConnectionError(str(exc))

        if not is_valid:
            raise ConnectionError(f"Failed to connect to {connection.name}")

        self.connections[connection.id] = connection
        self._initialize_engine(connection)

        return connection.id

    async def test_connection(self, connection: DatabaseConnection) -> bool:
        """Test database connection"""
        temp_engine = self._create_engine(connection)
        try:
            validation_query = self._get_validation_query(connection.type)
        except NotImplementedError as not_supported:
            print(str(not_supported))
            await temp_engine.dispose()
            return False

        try:
            async with temp_engine.connect() as conn:
                await conn.execute(text(validation_query))
            return True
        except SQLAlchemyError as exc:
            print(f"Connection test failed for {connection.name}: {exc}")
            return False
        finally:
            await temp_engine.dispose()

    def _initialize_engine(self, connection: DatabaseConnection) -> None:
        """Initialize the SQLAlchemy engine for the connection."""
        engine = self._create_engine(connection)
        self.engines[connection.id] = engine

    async def get_connection(self, connection_id: str) -> Optional[DatabaseConnection]:
        """Get database connection by ID"""
        await asyncio.sleep(0)
        return self.connections.get(connection_id)

    async def list_connections(self) -> List[DatabaseConnection]:
        """List all database connections"""
        await asyncio.sleep(0)
        return list(self.connections.values())

    async def remove_connection(self, connection_id: str) -> bool:
        """Remove database connection"""
        connection = self.connections.pop(connection_id, None)
        engine = self.engines.pop(connection_id, None)
        if connection is None:
            return False

        if connection_id in self.schema_cache:
            del self.schema_cache[connection_id]

        if engine is not None:
            await engine.dispose()

        return True

    async def get_database_schema(self, connection_id: str, refresh: bool = False) -> Optional[DatabaseSchema]:
        """Get database schema with caching"""
        if not refresh and connection_id in self.schema_cache:
            return self.schema_cache[connection_id]

        connection = await self.get_connection(connection_id)
        if not connection:
            return None

        schema = await self._introspect_schema(connection)
        self.schema_cache[connection_id] = schema
        return schema

    async def _introspect_schema(self, connection: DatabaseConnection) -> DatabaseSchema:
        """Introspect database schema"""
        engine = self.engines.get(connection.id)
        if not engine:
            raise ValueError("Connection not initialized")

        async with engine.connect() as conn:
            return await conn.run_sync(self._sync_introspect_schema, connection)

    def _sync_introspect_schema(self, sync_conn: Connection, connection: DatabaseConnection) -> DatabaseSchema:
        inspector: Inspector = inspect(sync_conn)
        schemas: List[str] = list(inspector.get_schema_names())
        tables: List[TableSchema] = []
        for schema_name in schemas:
            for table_name in inspector.get_table_names(schema=schema_name):
                columns = cast(List[Dict[str, Any]], inspector.get_columns(table_name, schema=schema_name))
                pk_constraint = inspector.get_pk_constraint(table_name, schema=schema_name)
                primary_keys = pk_constraint['constrained_columns'] if pk_constraint else []
                foreign_keys = cast(List[Dict[str, Any]], inspector.get_foreign_keys(table_name, schema=schema_name))
                indexes = cast(List[Dict[str, Any]], inspector.get_indexes(table_name, schema=schema_name))
                tables.append(
                    TableSchema(
                        name=table_name,
                        schema=schema_name,
                        columns=columns,
                        primary_keys=primary_keys,
                        foreign_keys=foreign_keys,
                        indexes=indexes
                    )
                )

        return DatabaseSchema(
            connection_id=connection.id,
            schemas=schemas,
            tables=tables,
            views=[],  # inspector.get_view_names() is not async yet
            functions=[],
            procedures=[]
        )

    async def execute_query(self, connection_id: str, query: str, limit: int = 1000) -> Dict[str, Any]:
        """Execute query against database"""
        engine = self.engines.get(connection_id)
        if not engine:
            raise ValueError(f"Connection {connection_id} not found or not initialized")

        async with engine.connect() as conn:
            result = await conn.execute(text(query))
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchmany(limit)]
            row_count = len(rows)

        return {
            "columns": columns,
            "rows": rows,
            "row_count": row_count,
            "execution_time_ms": 100  # Placeholder
        }

# Global instance
db_manager = DatabaseConnectionManager()
