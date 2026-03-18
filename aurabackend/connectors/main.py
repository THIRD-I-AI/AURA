"""
AURA Connector Service - Database connection management
Handles PostgreSQL, MySQL, and BigQuery connections
"""

import os
import sys
from typing import Dict, Any, List

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import (
    ConnectorConfig,
    SourceType,
    PostgreSQLConnector,
    MySQLConnector,
    BigQueryConnector,
)

app = FastAPI(
    title="AURA Connector Service",
    description="Database connection and data source management",
)

# CORS Configuration
_cors_origins = os.getenv(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ==================== Health ====================

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "connector",
        "version": "1.0.0",
    }


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
        if connector_type == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif connector_type == "mysql":
            connector = MySQLConnector(connector_config)
        elif connector_type == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            return ConnectorTestResponse(
                success=False,
                message=f"Unknown connector type: {connector_type}",
                error=f"Unsupported connector: {connector_type}",
            )

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
        if connector_type == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif connector_type == "mysql":
            connector = MySQLConnector(connector_config)
        elif connector_type == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            raise ValueError(f"Unknown connector type: {connector_type}")

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
                "description": "PostgreSQL database",
                "icon": "🐘",
            },
            {
                "id": "mysql",
                "name": "MySQL",
                "description": "MySQL database",
                "icon": "🐬",
            },
            {
                "id": "bigquery",
                "name": "Google BigQuery",
                "description": "BigQuery data warehouse",
                "icon": "☁️",
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
        if connector_type == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif connector_type == "mysql":
            connector = MySQLConnector(connector_config)
        elif connector_type == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            raise ValueError(f"Unknown connector type: {connector_type}")

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
