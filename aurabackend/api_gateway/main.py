"""Enhanced AURA API Gateway with connectors, safety, and insights"""

import os
import sys
import shutil
from typing import Dict, List, Any, Optional
from fastapi import HTTPException, UploadFile, File, status
from pydantic import BaseModel, Field
import httpx
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.service_factory import create_service
from shared.logging_config import get_logger
from connectors import (
    ConnectorConfig,
    SourceType,
    PostgreSQLConnector,
    MySQLConnector,
    BigQueryConnector,
)
from safety import SQLSafetyValidator
from insights import InsightsEngine

logger = get_logger("aura.api_gateway")

# Agentic DE framework
try:
    from agents.api import router as agent_router
    _AGENT_AVAILABLE = True
except ImportError:
    _AGENT_AVAILABLE = False


app = create_service(
    name="API Gateway",
    service_tag="api_gateway",
    description="Enterprise data analytics platform gateway",
)

# Mount the agent router
if _AGENT_AVAILABLE:
    app.include_router(agent_router)


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


# ==================== Legacy Endpoints from main.py ====================

# Import additional dependencies from main.py
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Import metadata repository
try:
    from metadata_store.repository import get_repository
except ImportError as e:
    print(f"Warning: Metadata repository not available - {e}")
    get_repository = None

# Import semantic builder
try:
    from semantic_builder import semantic_builder
except ImportError as e:
    print(f"Warning: Semantic builder not available - {e}")
    semantic_builder = None


# Additional Models from main.py
class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = None


class QueryRequest(BaseModel):
    session_id: str
    prompt: str
    context: Optional[str] = None


class SemanticFieldPayload(BaseModel):
    id: Optional[str] = None
    name: str
    field_type: str = Field(default="dimension", description="dimension | measure")
    data_type: Optional[str] = None
    expression: Optional[str] = None
    description: Optional[str] = None
    aggregation: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticModelPayload(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    fields: List[SemanticFieldPayload] = Field(default_factory=list)


@app.get("/")
def root():
    """Root endpoint"""
    return {"message": "AURA API Gateway is running.", "version": "2.0.0"}


@app.get("/files/supported-formats")
def get_supported_formats() -> Dict[str, Any]:
    """Get list of supported file formats"""
    try:
        return {
            "status": "success",
            "supported_formats": {
                "csv": {"extensions": [".csv"], "description": "Comma-separated values", "icon": "📊"},
                "excel": {"extensions": [".xlsx", ".xls"], "description": "Microsoft Excel", "icon": "📈"},
                "json": {"extensions": [".json"], "description": "JavaScript Object Notation", "icon": "🔗"},
                "text": {"extensions": [".txt"], "description": "Plain text files", "icon": "📄"},
                "parquet": {"extensions": [".parquet"], "description": "Apache Parquet columnar storage", "icon": "🗃️"}
            },
            "max_file_size": "25MB",
            "notes": {
                "parquet": "Optimized for analytics workloads, supports compression and efficient querying",
                "csv": "Most common format, human-readable",
                "excel": "Supports multiple sheets and formatting",
                "json": "Flexible structure, good for nested data"
            }
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/chat")
async def chat_endpoint(request: ChatRequest) -> Dict[str, Any]:
    """Main chat endpoint for AI interactions"""
    try:
        return {
            "response": f"Received your message: {request.message}",
            "confidence": 0.95,
            "suggestions": ["Try asking about data analysis", "Connect to a database", "Create visualizations"],
            "metadata": {"timestamp": datetime.now().isoformat(), "service": "api_gateway"}
        }
    except Exception as e:
        return {"error": str(e), "status": "error"}


@app.post("/generate_query")
async def generate_query_proxy(request: QueryRequest) -> Dict[str, Any]:
    """Proxy query generation to orchestration service."""
    target_url = os.getenv(
        "ORCHESTRATION_SERVICE_URL",
        "http://localhost:8006/v1/orchestrations/query",
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(target_url, json=request.model_dump())
            response.raise_for_status()
            payload = response.json()
            return payload
    except httpx.HTTPStatusError as http_exc:
        return {
            "status": "Error",
            "error_message": f"Orchestration error: {http_exc.response.text}",
            "final_query": "-- Error generating query",
        }
    except Exception as exc:
        error_msg = str(exc)
        print(f"[ERROR] generate_query_proxy: {error_msg}")
        return {
            "status": "Error",
            "error_message": f"Backend error: {error_msg}",
            "final_query": "-- Error generating query",
            "details": error_msg
        }


@app.get("/databases/test/{db_type}")
async def test_database_connection(db_type: str):
    """Proxy to database service for connection testing"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"http://localhost:8002/databases/test/{db_type}")
            return response.json()
    except Exception as e:
        return {"error": f"Database service unavailable: {str(e)}", "status": "error"}


# ==================== File Upload ====================

# Initialize file service
try:
    from shared.file_service import file_service
except ImportError as e:
    print(f"Warning: File service not available - {e}")
    file_service = None

@app.post("/upload")
async def upload_universal(
    file: UploadFile = File(None),  # Try standard name 'file'
    upload_file: UploadFile = File(None)  # Try alias 'upload_file'
):
    """
    Universal file upload endpoint - accepts both 'file' and 'upload_file' parameter names
    """
    # 1. Determine which key was used
    target_file = file or upload_file
    
    if not target_file:
        print("ERROR: No file received! Checked both 'file' and 'upload_file'.")
        raise HTTPException(status_code=422, detail="No file sent. Ensure formData uses key 'file'")
    
    print(f"DEBUG: Receiving file: {target_file.filename}")
    
    try:
        # 2. Create the folder safely
        os.makedirs("uploads", exist_ok=True)
        
        # 3. Save the file
        file_path = f"uploads/{target_file.filename}"
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(target_file.file, buffer)
        
        print(f"SUCCESS: Saved to {file_path}")
        
        return {
            "filename": target_file.filename,
            "status": "success",
            "message": "File uploaded successfully"
        }
    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server save failed: {str(e)}")


# ==================== Additional File Endpoints ====================

@app.get("/files")
async def list_files() -> Dict[str, Any]:
    """List all uploaded files"""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    
    try:
        files = file_service.list_files()
        return {"status": "success", "files": files}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/files/{file_id}")
async def get_file_info(file_id: str) -> Dict[str, Any]:
    """Get information about a specific file"""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    
    try:
        file_info = file_service.get_file_info(file_id)
        if file_info:
            return {"status": "success", "file_info": file_info}
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except HTTPException as e:
        raise e
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/files/{file_id}/profile")
async def get_file_profile(file_id: str) -> Dict[str, Any]:
    """Fetch stored profile for a file if available."""
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}

    try:
        async for repo in get_repository():
            profile = await repo.get_dataset_profile(file_id)
            break
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        return {
            "status": "success",
            "file_id": file_id,
            "dataset_name": profile.dataset_name,
            "rows_count": profile.rows_count,
            "columns_count": profile.columns_count,
            "profile": profile.profile,
            "updated_at": profile.updated_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.delete("/files/{file_id}")
async def delete_file(file_id: str) -> Dict[str, Any]:
    """Delete a file and its processed data"""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    
    try:
        success = file_service.delete_file(file_id)
        if success:
            return {"status": "success", "message": "File deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="File not found or deletion failed")
    except HTTPException as e:
        raise e
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ==================== Semantic Model Endpoints ====================

def _serialize_semantic_model(model: Any) -> Dict[str, Any]:
    """Helper to serialize semantic model"""
    return {
        "id": model.id,
        "name": model.name,
        "description": model.description,
        "source": model.source,
        "tags": model.tags,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
        "fields": [
            {
                "id": field.id,
                "name": field.name,
                "field_type": field.field_type,
                "data_type": field.data_type,
                "expression": field.expression,
                "description": field.description,
                "aggregation": field.aggregation,
                "metadata": field.metadata,
                "created_at": field.created_at,
                "updated_at": field.updated_at,
            }
            for field in getattr(model, "fields", [])
        ],
    }


@app.post("/semantic/models/from-file/{file_id}")
async def auto_generate_model_from_file(file_id: str) -> Dict[str, Any]:
    """Auto-generate semantic model from dataset profile (no hardcoding)."""
    if semantic_builder is None or get_repository is None:
        return {"status": "error", "error": "Semantic builder or repository not available"}

    try:
        async for repo in get_repository():
            # Fetch the stored profile
            profile_record = await repo.get_dataset_profile(file_id)
            if profile_record is None:
                raise HTTPException(status_code=404, detail="Dataset profile not found")

            # Auto-generate model payload from profile
            model_payload = semantic_builder.generate_model_from_profile(
                file_id=file_id,
                dataset_name=profile_record.dataset_name or f"dataset_{file_id[:8]}",
                profile=profile_record.profile,
            )

            # Persist the generated model
            model = await repo.upsert_semantic_model(
                model_id=None,
                name=model_payload['name'],
                description=model_payload['description'],
                source=model_payload['source'],
                tags=model_payload['tags'],
                fields=model_payload['fields'],
            )

            break

        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/semantic/models")
async def upsert_semantic_model(payload: SemanticModelPayload) -> Dict[str, Any]:
    """Create or update a semantic model"""
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}

    try:
        async for repo in get_repository():
            model = await repo.upsert_semantic_model(
                model_id=payload.id,
                name=payload.name,
                description=payload.description,
                source=payload.source,
                tags=payload.tags,
                fields=[field.model_dump() for field in payload.fields],
            )
            break
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/semantic/models")
async def list_semantic_models() -> Dict[str, Any]:
    """List all semantic models"""
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}

    try:
        async for repo in get_repository():
            models = await repo.list_semantic_models()
            break
        return {"status": "success", "models": [_serialize_semantic_model(model) for model in models]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/semantic/models/{model_id}")
async def get_semantic_model(model_id: str) -> Dict[str, Any]:
    """Get a specific semantic model"""
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}

    try:
        async for repo in get_repository():
            model = await repo.get_semantic_model(model_id)
            break
        if model is None:
            raise HTTPException(status_code=404, detail="Semantic model not found")
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ==================== Connections ====================

@app.get("/connections")
async def get_connections():
    """
    Get active data source connections
    Returns stub data until full connector implementation is ready
    """
    return {
        "success": True,
        "connections": [],  # Empty for now - will be populated when connector service is integrated
        "count": 0,
        "message": "No active connections. Upload a file to get started."
    }


# ── Health is provided by create_service() ──


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
