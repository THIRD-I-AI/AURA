"""
Enhanced AURA API Gateway with connectors, safety, and insights
"""

import os
import sys
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import (
    ConnectorConfig,
    SourceType,
    PostgreSQLConnector,
    MySQLConnector,
    BigQueryConnector,
)
from safety import SQLSafetyValidator, QueryRiskLevel
from insights import InsightsEngine
from shared.file_service import FileService
from metadata_store.repository import MetadataRepository
from metadata_store.db import get_session
from semantic_builder import SemanticModelBuilder


app = FastAPI(
    title="AURA API Gateway",
    description="Enterprise data analytics platform gateway",
    version="2.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Models ====================

class ConnectorMetadataResponse(BaseModel):
    """Connector metadata"""
    source_id: str
    source_type: str
    display_name: str
    description: str
    icon: str
    connected: bool
    last_sync: Optional[str]
    table_count: int


class ValidateQueryRequest(BaseModel):
    """Query validation request"""
    query: str
    dry_run_mode: bool = False
    max_rows: int = 10000


class ValidateQueryResponse(BaseModel):
    """Query validation response"""
    is_valid: bool
    risk_level: str
    warnings: List[str]
    errors: List[str]
    suggested_query: Optional[str]
    row_count_estimate: int
    estimated_execution_ms: Optional[float]


class ExecuteQueryRequest(BaseModel):
    """Query execution request"""
    query: str
    connector_type: str
    connector_config: Dict[str, Any]
    dry_run: bool = False


class ExecuteQueryResponse(BaseModel):
    """Query execution response"""
    success: bool
    data: Optional[List[Dict[str, Any]]]
    rows: int
    columns: List[str]
    insights: Optional[Dict[str, Any]]
    error: Optional[str]
    execution_time_ms: float


class ConnectorTableListResponse(BaseModel):
    """List of tables from connector"""
    connector_id: str
    tables: List[str]
    total_count: int


class ProfileTableRequest(BaseModel):
    """Request to profile a table"""
    connector_type: str
    connector_config: Dict[str, Any]
    table_name: str


# ==================== Connectors API ====================

@app.get("/connectors/available")
async def list_available_connectors():
    """List available data connectors"""
    return {
        "connectors": [
            {
                "id": "postgresql",
                "name": "PostgreSQL",
                "description": "PostgreSQL database connector",
                "icon": "🐘",
                "config_required": ["host", "port", "username", "password", "database"],
            },
            {
                "id": "mysql",
                "name": "MySQL",
                "description": "MySQL database connector",
                "icon": "🐬",
                "config_required": ["host", "port", "username", "password", "database"],
            },
            {
                "id": "bigquery",
                "name": "Google BigQuery",
                "description": "BigQuery data warehouse connector",
                "icon": "☁️",
                "config_required": ["credentials_json", "database"],
            },
        ]
    }


@app.post("/connectors/{connector_type}/test")
async def test_connector(connector_type: str, config: Dict[str, Any]):
    """Test connector configuration"""
    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"test-{connector_type}",
            **config,
        )

        # Create appropriate connector
        if connector_type == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif connector_type == "mysql":
            connector = MySQLConnector(connector_config)
        elif connector_type == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            raise ValueError(f"Unknown connector type: {connector_type}")

        # Test connection
        connected = await connector.connect()
        if connected:
            tables = await connector.list_tables()
            await connector.disconnect()
            return {
                "success": True,
                "message": f"Connected successfully. Found {len(tables)} tables.",
                "table_count": len(tables),
            }
        else:
            return {
                "success": False,
                "message": "Failed to connect",
                "error": "Connection failed",
            }
    except Exception as e:
        return {
            "success": False,
            "message": "Test failed",
            "error": str(e),
        }


@app.post("/connectors/{connector_type}/tables")
async def list_connector_tables(
    connector_type: str,
    config: Dict[str, Any],
) -> ConnectorTableListResponse:
    """List tables from a connector"""
    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"list-{connector_type}",
            **config,
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

        return ConnectorTableListResponse(
            connector_id=f"test-{connector_type}",
            tables=tables,
            total_count=len(tables),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@app.post("/connectors/{connector_type}/profile")
async def profile_table(
    connector_type: str,
    request: ProfileTableRequest,
) -> Dict[str, Any]:
    """Profile a table from connector"""
    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"profile-{connector_type}",
            **request.connector_config,
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

        # Profile table
        await connector.connect()
        profile = await connector.profile_table(request.table_name)
        await connector.disconnect()

        return profile
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# ==================== Safety & Validation API ====================

@app.post("/validate/query", response_model=ValidateQueryResponse)
async def validate_query(request: ValidateQueryRequest):
    """Validate SQL query for safety and performance"""
    validator = SQLSafetyValidator(
        max_rows=request.max_rows,
        dry_run_only=request.dry_run_mode,
    )

    result = validator.validate(request.query)

    # Estimate execution time
    from safety import QueryPlanner
    exec_time, explanation = QueryPlanner.estimate_execution_time(request.query)

    return ValidateQueryResponse(
        is_valid=result.is_valid,
        risk_level=result.risk_level.value,
        warnings=result.warnings,
        errors=result.errors,
        suggested_query=result.suggested_query,
        row_count_estimate=result.row_count_estimate,
        estimated_execution_ms=exec_time,
    )


@app.post("/lint/query")
async def lint_query(query: str):
    """Lint SQL query for style and optimization"""
    validator = SQLSafetyValidator()
    suggestions = validator.lint_query(query)

    return {
        "suggestions": suggestions,
        "suggested_query": validator.add_safety_limit(query) if "LIMIT" not in query.upper() else None,
    }


# ==================== Insights API ====================

@app.post("/analyze/results")
async def analyze_results(
    query: str,
    results: List[Dict[str, Any]],
    column_profiles: Optional[Dict[str, Any]] = None,
):
    """Generate insights from query results"""
    engine = InsightsEngine()
    analysis = engine.analyze(query, results, column_profiles)
    return analysis


@app.post("/execute/query", response_model=ExecuteQueryResponse)
async def execute_query_with_insights(request: ExecuteQueryRequest):
    """Execute query with automatic insights generation"""
    try:
        # Validate first
        validator = SQLSafetyValidator()
        validation = validator.validate(request.query)

        if not validation.is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Query validation failed: {validation.errors}",
            )

        # Create connector and execute
        connector_config = ConnectorConfig(
            source_type=SourceType(request.connector_type),
            name=f"exec-{request.connector_type}",
            **request.connector_config,
        )

        if request.connector_type == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif request.connector_type == "mysql":
            connector = MySQLConnector(connector_config)
        elif request.connector_type == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            raise ValueError(f"Unknown connector type: {request.connector_type}")

        # Execute
        import time
        start_time = time.time()

        await connector.connect()
        results = await connector.execute_query(request.query)
        await connector.disconnect()

        execution_time = (time.time() - start_time) * 1000

        # Generate insights
        engine = InsightsEngine()
        columns = list(results[0].keys()) if results else []
        insights = engine.analyze(request.query, results) if results else None

        return ExecuteQueryResponse(
            success=True,
            data=results,
            rows=len(results),
            columns=columns,
            insights=insights,
            execution_time_ms=execution_time,
        )

    except Exception as e:
        return ExecuteQueryResponse(
            success=False,
            data=None,
            rows=0,
            columns=[],
            error=str(e),
            execution_time_ms=0,
        )


# ==================== Health ====================

@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "healthy",
        "service": "api-gateway",
        "version": "2.0.0",
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
