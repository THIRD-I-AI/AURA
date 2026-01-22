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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
