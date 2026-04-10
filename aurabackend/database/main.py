"""
AURA Database Service
RESTful API for database connections and schema management
"""

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .connection_manager import DatabaseConnection, DatabaseType, db_manager

app = FastAPI(
    title="AURA Database Service",
    description="Enterprise database connectivity and schema management",
    version="1.0.0"
)


@app.get("/")
async def root() -> Dict[str, Any]:
    """Default route for quick service discovery."""
    return {
        "service": "database",
        "status": "available",
        "endpoints": {
            "health": "/health",
            "connections": "/connections",
            "supported_databases": "/supported-databases"
        }
    }

CONNECTION_NOT_FOUND_MSG = "Connection not found"

# CORS middleware for frontend integration
_default_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("DATABASE_ALLOWED_ORIGINS", ",".join(_default_origins)).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for API requests/responses
class DatabaseConnectionRequest(BaseModel):
    name: str = Field(..., description="Connection name")
    type: DatabaseType = Field(..., description="Database type")
    host: Optional[str] = Field(None, description="Database host")
    port: Optional[int] = Field(None, description="Database port")
    database: Optional[str] = Field(None, description="Database name or file path")
    username: Optional[str] = Field(None, description="Username (optional for SQLite)")
    password: Optional[str] = Field(None, description="Password (optional for SQLite)")
    ssl_enabled: bool = Field(default=False, description="Enable SSL")
    connection_string: Optional[str] = Field(None, description="Custom connection string")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")

class DatabaseConnectionResponse(BaseModel):
    id: str
    name: str
    type: DatabaseType
    host: str
    port: int
    database: str
    username: str
    ssl_enabled: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]

class QueryRequest(BaseModel):
    connection_id: str = Field(..., description="Database connection ID")
    query: str = Field(..., description="SQL query to execute")
    limit: int = Field(default=1000, description="Result limit")

class QueryResponse(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    execution_time_ms: int

class SchemaResponse(BaseModel):
    connection_id: str
    schemas: List[str]
    tables: List[Dict[str, Any]]
    views: List[Dict[str, Any]]
    functions: List[Dict[str, Any]]
    procedures: List[Dict[str, Any]]
    last_updated: datetime

# File metadata models
class FileMetadata(BaseModel):
    file_id: str = Field(..., description="Unique file identifier")
    original_filename: str = Field(..., description="Original filename")
    stored_filename: str = Field(..., description="Filename in storage")
    content_type: str = Field(..., description="MIME type")
    file_extension: str = Field(..., description="File extension")
    file_size: int = Field(..., description="File size in bytes")
    file_hash: str = Field(..., description="SHA256 hash")
    upload_time: datetime = Field(..., description="Upload timestamp")
    status: str = Field(..., description="Processing status")
    rows_count: Optional[int] = Field(None, description="Number of data rows")
    columns_count: Optional[int] = Field(None, description="Number of columns")
    processed_time: Optional[datetime] = Field(None, description="Processing timestamp")
    error_message: Optional[str] = Field(None, description="Error if processing failed")

class FileUploadResponse(BaseModel):
    file_id: str
    message: str
    status: str

# API Endpoints

@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint"""
    return {"status": "healthy", "service": "database", "timestamp": datetime.now()}

@app.post("/connections", response_model=Dict[str, str])
async def create_connection(request: DatabaseConnectionRequest):
    """Create a new database connection"""
    try:
        connection = DatabaseConnection(
            id=str(uuid.uuid4()),
            name=request.name,
            type=request.type,
            host=request.host,
            port=request.port,
            database=request.database,
            username=request.username,
            password=request.password,
            ssl_enabled=request.ssl_enabled,
            connection_string=request.connection_string,
            metadata=request.metadata or {}
        )

        connection_id = await db_manager.add_connection(connection)
        return {"connection_id": connection_id, "message": "Connection created successfully"}

    except ConnectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotImplementedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/connections", response_model=List[DatabaseConnectionResponse])
async def list_connections():
    """List all database connections"""
    try:
        connections = await db_manager.list_connections()
        return [
            DatabaseConnectionResponse(
                id=conn.id,
                name=conn.name,
                type=conn.type,
                host=conn.host,
                port=conn.port,
                database=conn.database,
                username=conn.username,
                ssl_enabled=conn.ssl_enabled,
                is_active=conn.is_active,
                created_at=conn.created_at or datetime.now(),
                updated_at=conn.updated_at or datetime.now(),
                metadata=conn.metadata or {}
            )
            for conn in connections
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/connections/{connection_id}", response_model=DatabaseConnectionResponse)
async def get_connection(connection_id: str):
    """Get a specific database connection"""
    try:
        connection = await db_manager.get_connection(connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail=CONNECTION_NOT_FOUND_MSG)

        return DatabaseConnectionResponse(
            id=connection.id,
            name=connection.name,
            type=connection.type,
            host=connection.host,
            port=connection.port,
            database=connection.database,
            username=connection.username,
            ssl_enabled=connection.ssl_enabled,
            is_active=connection.is_active,
            created_at=connection.created_at or datetime.now(),
            updated_at=connection.updated_at or datetime.now(),
            metadata=connection.metadata or {}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str):
    """Delete a database connection"""
    try:
        success = await db_manager.remove_connection(connection_id)
        if not success:
            raise HTTPException(status_code=404, detail=CONNECTION_NOT_FOUND_MSG)

        return {"message": "Connection deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/connections/{connection_id}/test")
async def test_connection(connection_id: str) -> Dict[str, Any]:
    """Test database connection"""
    try:
        connection = await db_manager.get_connection(connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail=CONNECTION_NOT_FOUND_MSG)

        is_valid = await db_manager.test_connection(connection)
        return {
            "connection_id": connection_id,
            "is_valid": is_valid,
            "message": "Connection successful" if is_valid else "Connection failed"
        }
    except NotImplementedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/connections/{connection_id}/schema", response_model=SchemaResponse)
async def get_schema(connection_id: str, refresh: bool = False):
    """Get database schema"""
    try:
        schema = await db_manager.get_database_schema(connection_id, refresh=refresh)
        if not schema:
            raise HTTPException(status_code=404, detail="Connection not found or schema unavailable")

        return SchemaResponse(
            connection_id=schema.connection_id,
            schemas=schema.schemas,
            tables=[
                {
                    "name": table.name,
                    "schema": table.schema,
                    "columns": table.columns,
                    "primary_keys": table.primary_keys,
                    "foreign_keys": table.foreign_keys,
                    "indexes": table.indexes,
                    "row_count": table.row_count,
                    "table_type": table.table_type,
                    "description": table.description
                }
                for table in schema.tables
            ],
            views=[
                {
                    "name": view.name,
                    "schema": view.schema,
                    "columns": view.columns,
                    "table_type": view.table_type,
                    "description": view.description
                }
                for view in schema.views
            ],
            functions=schema.functions,
            procedures=schema.procedures,
            last_updated=schema.last_updated or datetime.now()
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/connections/{connection_id}/query", response_model=QueryResponse)
async def execute_query(connection_id: str, request: QueryRequest):
    """Execute SQL query"""
    try:
        if request.connection_id != connection_id:
            raise HTTPException(status_code=400, detail="Connection ID mismatch")

        result = await db_manager.execute_query(
            connection_id=connection_id,
            query=request.query,
            limit=request.limit
        )

        return QueryResponse(
            columns=result["columns"],
            rows=result["rows"],
            row_count=result["row_count"],
            execution_time_ms=result["execution_time_ms"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/supported-databases")
async def get_supported_databases() -> Dict[str, List[Dict[str, Any]]]:
    """Get list of supported database types"""
    return {
        "databases": [
            {
                "type": db_type.value,
                "name": db_type.value.replace("_", " ").title(),
                "default_port": _get_default_port(db_type),
                "supports_ssl": True,
                "description": _get_database_description(db_type)
            }
            for db_type in DatabaseType
        ]
    }

def _get_default_port(db_type: DatabaseType) -> int:
    """Get default port for database type"""
    port_map = {
        DatabaseType.POSTGRESQL: 5432,
        DatabaseType.MYSQL: 3306,
        DatabaseType.MSSQL: 1433,
        DatabaseType.ORACLE: 1521,
        DatabaseType.MONGODB: 27017,
        DatabaseType.SNOWFLAKE: 443,
        DatabaseType.BIGQUERY: 443,
        DatabaseType.REDSHIFT: 5439,
        DatabaseType.DATABRICKS: 443,
        DatabaseType.CLICKHOUSE: 8123,
        DatabaseType.CASSANDRA: 9042,
        DatabaseType.SQLITE: 0
    }
    return port_map.get(db_type, 5432)

def _get_database_description(db_type: DatabaseType) -> str:
    """Get description for database type"""
    descriptions = {
        DatabaseType.POSTGRESQL: "Advanced open-source relational database",
        DatabaseType.MYSQL: "Popular open-source relational database",
        DatabaseType.SQLITE: "Lightweight file-based database",
        DatabaseType.MSSQL: "Microsoft SQL Server",
        DatabaseType.ORACLE: "Enterprise database system",
        DatabaseType.MONGODB: "Document-oriented NoSQL database",
        DatabaseType.SNOWFLAKE: "Cloud-native data warehouse",
        DatabaseType.BIGQUERY: "Google Cloud data warehouse",
        DatabaseType.REDSHIFT: "Amazon data warehouse",
        DatabaseType.DATABRICKS: "Unified analytics platform",
        DatabaseType.CLICKHOUSE: "Column-oriented database for analytics",
        DatabaseType.CASSANDRA: "Distributed NoSQL database"
    }
    return descriptions.get(db_type, "Database system")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("DATABASE_PORT", "8002")),
    )
