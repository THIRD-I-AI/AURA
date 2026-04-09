"""
AURA Connector Service - Database connection management
Handles PostgreSQL, MySQL, BigQuery, and DuckDB connections
"""

import os
import sys
from typing import Dict, Any, List

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

# Add parent directory to path for imports

from shared.service_factory import create_service
from shared.logging_config import get_logger
from connectors import (
    ConnectorConfig,
    SourceType,
    PostgreSQLConnector,
    MySQLConnector,
    BigQueryConnector,
    DuckDBConnector,
)

logger = get_logger("aura.connectors")

app = create_service(
    name="Connectors",
    service_tag="connectors",
    description="Database connection and data source management",
)


# ==================== Connector Factory ====================

def _create_connector(connector_type: str, config: ConnectorConfig):
    """Return the right connector class for a given type string."""
    _map = {
        "postgresql": PostgreSQLConnector,
        "mysql": MySQLConnector,
        "bigquery": BigQueryConnector,
        "duckdb": DuckDBConnector,
    }
    cls = _map.get(connector_type.lower())
    if cls is None:
        raise ValueError(f"Unknown connector type: {connector_type}")
    return cls(config)


# ==================== Models ====================

class ConnectorTestRequest(BaseModel):
    """Request to test a connector"""
    connector_type: str = Field(..., description="postgresql, mysql, or bigquery")
    config: Dict[str, Any] = Field(..., description="Connector configuration")


class ConnectorTestResponse(BaseModel):
    """Response from connector test"""
    success: bool
    message: str
    table_count: int = 0
    error: str = ""


class TableListRequest(BaseModel):
    """Request to list tables from a connector"""
    connector_type: str
    config: Dict[str, Any]


class TableListResponse(BaseModel):
    """List of tables from a connector"""
    connector_id: str
    tables: List[str]
    total_count: int


# Health is provided by create_service()


# ==================== Connector Operations ====================

@app.post("/test", response_model=ConnectorTestResponse)
async def test_connector(request: ConnectorTestRequest):
    """Test a database connection"""
    try:
        connector_type = request.connector_type.lower()
        
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"test-{connector_type}",
            **request.config,
        )

        # Create appropriate connector
        connector = _create_connector(connector_type, connector_config)

        # Test connection
        connected = await connector.connect()
        if connected:
            tables = await connector.list_tables()
            await connector.disconnect()
            return ConnectorTestResponse(
                success=True,
                message=f"Connected successfully. Found {len(tables)} tables.",
                table_count=len(tables),
            )
        else:
            return ConnectorTestResponse(
                success=False,
                message="Failed to connect",
                error="Connection test failed",
            )
            
    except Exception as e:
        return ConnectorTestResponse(
            success=False,
            message=f"Test failed: {str(e)}",
            error=str(e),
        )


@app.post("/tables", response_model=TableListResponse)
async def list_tables(request: TableListRequest):
    """List tables from a connector"""
    try:
        connector_type = request.connector_type.lower()
        
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"list-{connector_type}",
            **request.config,
        )

        # Create connector
        connector = _create_connector(connector_type, connector_config)

        # List tables
        await connector.connect()
        tables = await connector.list_tables()
        await connector.disconnect()

        return TableListResponse(
            connector_id=f"test-{connector_type}",
            tables=tables,
            total_count=len(tables),
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@app.get("/connectors/available")
async def list_available_connectors():
    """List available connector types"""
    return {
        "connectors": [
            {
                "id": "postgresql",
                "name": "PostgreSQL",
                "description": "PostgreSQL database (+ pgvector, PostGIS)",
                "icon": "🐘",
                "capabilities": ["sql", "vector", "spatial"],
            },
            {
                "id": "mysql",
                "name": "MySQL",
                "description": "MySQL database",
                "icon": "🐬",
                "capabilities": ["sql"],
            },
            {
                "id": "bigquery",
                "name": "Google BigQuery",
                "description": "BigQuery data warehouse",
                "icon": "☁️",
                "capabilities": ["sql"],
            },
            {
                "id": "duckdb",
                "name": "DuckDB",
                "description": "Fast local analytics — query CSV/Parquet/JSON directly",
                "icon": "🦆",
                "capabilities": ["sql", "file_query"],
            },
        ]
    }


# ==================== Agent Tool Endpoints ====================

class IngestRequest(BaseModel):
    """Request to ingest a file and profile it"""
    file_path: str = Field(..., description="Path to the file to ingest")


class IntrospectRequest(BaseModel):
    """Request to introspect a database schema"""
    connector_type: str = Field(..., description="postgresql, mysql, or bigquery")
    config: Dict[str, Any] = Field(default_factory=dict, description="Connector configuration")


@app.post("/ingest")
async def ingest_file(request: IngestRequest):
    """Ingest a file and return basic profiling information.

    Used by the agentic DE framework to ingest data files.
    """
    import os as _os
    file_path = request.file_path

    if not _os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {file_path}",
        )

    # Basic profiling with pandas if available
    try:
        import pandas as pd

        ext = _os.path.splitext(file_path)[1].lower()
        if ext == ".csv":
            df = pd.read_csv(file_path, nrows=10_000)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, nrows=10_000)
        elif ext == ".json":
            df = pd.read_json(file_path)
        elif ext == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            return {
                "file_path": file_path,
                "status": "unsupported_format",
                "message": f"Unsupported file extension: {ext}",
            }

        profile = {
            "file_path": file_path,
            "rows": len(df),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "null_counts": df.isnull().sum().to_dict(),
            "sample": df.head(5).to_dict(orient="records"),
            "status": "success",
        }
        return profile

    except ImportError:
        return {
            "file_path": file_path,
            "status": "limited",
            "message": "pandas not available — returning file metadata only",
            "size_bytes": _os.path.getsize(file_path),
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingest failed: {str(e)}",
        )


@app.post("/introspect")
async def introspect_database(request: IntrospectRequest):
    """Introspect a database and return schema metadata.

    Used by the agentic DE framework to discover tables and columns.
    """
    try:
        connector_type = request.connector_type.lower()

        if not request.config:
            return {
                "connector_type": connector_type,
                "tables": [],
                "message": "No config provided — cannot connect",
            }

        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"introspect-{connector_type}",
            **request.config,
        )

        # Create connector
        connector = _create_connector(connector_type, connector_config)

        # Connect and discover schema
        await connector.connect()
        tables = await connector.list_tables()

        schema_info: List[Dict[str, Any]] = []
        for table_name in tables:
            try:
                profile = await connector.profile_table(table_name)
                schema_info.append({
                    "table": table_name,
                    "profile": profile,
                })
            except Exception:
                schema_info.append({
                    "table": table_name,
                    "profile": None,
                })

        await connector.disconnect()

        return {
            "connector_type": connector_type,
            "tables": tables,
            "table_count": len(tables),
            "schema": schema_info,
            "status": "success",
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Introspection failed: {str(e)}",
        )


# ==================== Query Execution Endpoint ====================
# Used by the Execution Sandbox (port 8003) to run SQL against a connection.

class QueryRequest(BaseModel):
    """Request to execute a SQL query"""
    connection_id: str = Field(default="default", description="Connection identifier")
    query: str = Field(..., description="SQL query to execute")
    limit: int = Field(default=1000, ge=1, le=100_000)


# In-memory connection store (maps connection_id → connector config)
_connection_store: Dict[str, Dict[str, Any]] = {}


@app.post("/connections/{connection_id}/query")
async def execute_query(connection_id: str, request: QueryRequest):
    """Execute a SQL query against a stored or default connection.

    This is the endpoint the Execution Sandbox proxies to.
    """
    # Look up stored connection config, or use env defaults
    conn_cfg = _connection_store.get(connection_id)
    if not conn_cfg:
        # Try environment-variable based defaults
        db_host = os.getenv("DB_HOST", "")
        db_port = os.getenv("DB_PORT", "5432")
        db_user = os.getenv("DB_USER", "")
        db_pass = os.getenv("DB_PASSWORD", "")
        db_name = os.getenv("DB_NAME", "")
        db_type = os.getenv("DB_TYPE", "postgresql")

        if not db_host:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Connection '{connection_id}' not found and no default DB configured. "
                    "Set DB_HOST, DB_USER, DB_PASSWORD, DB_NAME env vars."
                ),
            )
        conn_cfg = {
            "connector_type": db_type,
            "host": db_host,
            "port": int(db_port),
            "username": db_user,
            "password": db_pass,
            "database": db_name,
        }

    connector_type = conn_cfg.pop("connector_type", "postgresql")
    config_copy = {k: v for k, v in conn_cfg.items() if k != "connector_type"}

    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"query-{connection_id}",
            **config_copy,
        )

        connector = _create_connector(connector_type, connector_config)

        await connector.connect()
        rows = await connector.execute_query(request.query, limit=request.limit)
        await connector.disconnect()

        # Derive columns from first row
        columns = list(rows[0].keys()) if rows else []

        return {
            "columns": columns,
            "rows": [list(r.values()) for r in rows],
            "row_count": len(rows),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {str(e)}",
        )


# ==================== Vault Endpoints ====================
# Expose the hybrid multimodal vault (regular + vector + spatial)
# through the connector service so the gateway can proxy them.

_vault_instance = None


async def _get_vault():
    """Lazy-init the AuraVault singleton."""
    global _vault_instance
    if _vault_instance is None:
        try:
            from shared.vault_client import AuraVault
            _vault_instance = AuraVault()
            await _vault_instance.connect()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Vault unavailable: {exc}",
            )
    return _vault_instance


@app.get("/vault/health")
async def vault_health():
    """Health check for the AURA Vault database."""
    vault = await _get_vault()
    info = await vault.health()
    caps = await vault.capabilities()
    return {"vault": info, "capabilities": caps}


@app.get("/vault/tables")
async def vault_tables():
    """List all tables in the vault."""
    vault = await _get_vault()
    tables = await vault._db.list_tables()
    return {"tables": tables, "count": len(tables)}


class VaultQueryRequest(BaseModel):
    query: str = Field(..., description="SQL query to run against the vault")
    limit: int = Field(default=1000, ge=1, le=100_000)


@app.post("/vault/query")
async def vault_query(request: VaultQueryRequest):
    """Execute a SQL query against the AURA Vault."""
    vault = await _get_vault()
    try:
        rows = await vault._db.execute_query(request.query, limit=request.limit)
        columns = list(rows[0].keys()) if rows else []
        return {
            "columns": columns,
            "rows": [list(r.values()) for r in rows],
            "row_count": len(rows),
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Vault query failed: {e}",
        )


class VectorSearchRequest(BaseModel):
    table: str = Field(default="image_assets", description="Table with embeddings")
    embedding: List[float] = Field(..., description="Query embedding vector")
    column: str = Field(default="embedding")
    limit: int = Field(default=10, ge=1, le=1000)
    metric: str = Field(default="cosine", description="cosine or l2")


@app.post("/vault/vector-search")
async def vault_vector_search(request: VectorSearchRequest):
    """Find nearest neighbours by vector similarity (pgvector)."""
    vault = await _get_vault()
    try:
        results = await vault._db.vector_search(
            request.table,
            request.embedding,
            column=request.column,
            limit=request.limit,
            metric=request.metric,
        )
        return {"results": results, "count": len(results)}
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Vector search failed: {e}",
        )


class SpatialQueryRequest(BaseModel):
    query: str = Field(..., description="PostGIS-enabled SQL query")
    params: List[Any] = Field(default_factory=list)


@app.post("/vault/spatial-query")
async def vault_spatial_query(request: SpatialQueryRequest):
    """Run a spatial/PostGIS query against the vault."""
    vault = await _get_vault()
    try:
        results = await vault._db.spatial_query(
            request.query,
            request.params or None,
        )
        return {"results": results, "count": len(results)}
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Spatial query failed: {e}",
        )


@app.get("/vault/capabilities")
async def vault_capabilities():
    """Report which data-planes (relational, vector, spatial) are available."""
    vault = await _get_vault()
    return await vault.capabilities()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("CONNECTORS_PORT", "8002")),
    )
