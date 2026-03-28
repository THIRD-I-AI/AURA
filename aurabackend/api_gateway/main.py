"""Enhanced AURA API Gateway with connectors, safety, and insights"""

import json
import os
import re
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


# ==================== Lightweight Execute (frontend chat flow) ====================

class _ChatExecuteRequest(BaseModel):
    """Lightweight execution request from the chat UI."""
    sql: str
    connection_id: Optional[str] = None


@app.post("/execute")
async def execute_for_chat(req: _ChatExecuteRequest):
    """Execute SQL from the chat interface.

    • If *connection_id* is provided, proxy to the execution sandbox (port 8003)
      which talks to a real database via the connector service.
    • Otherwise, try to run the query against uploaded file data using DuckDB.
    """
    import time
    start = time.time()

    sql = req.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL query is required")

    # ── Safety gate ─────────────────────────────────────────────────────
    validator = SQLSafetyValidator()
    validation = validator.validate(sql)
    if not validation.is_valid:
        raise HTTPException(status_code=400, detail=f"Query blocked: {validation.errors}")

    # ── If there's a connection_id, proxy to the execution sandbox ──────
    if req.connection_id:
        sandbox_url = os.getenv("EXECUTION_SANDBOX_URL", "http://localhost:8003")
        payload = {
            "job_id": f"chat-{int(time.time()*1000)}",
            "sql": sql,
            "connection_id": req.connection_id,
            "approved": True,
            "limit": int(os.getenv("DEFAULT_QUERY_LIMIT", "1000")),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{sandbox_url}/execute_sql", json=payload)
                resp.raise_for_status()
                data = resp.json()
                # Convert columns+rows into array-of-dicts for the frontend
                columns = data.get("columns", [])
                rows = data.get("rows", [])
                records = [dict(zip(columns, row)) for row in rows]
                elapsed = (time.time() - start) * 1000
                return {
                    "success": True,
                    "data": records,
                    "columns": columns,
                    "row_count": len(records),
                    "execution_time_ms": round(elapsed, 1),
                    "chart_spec": data.get("chart_spec"),
                }
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            try:
                detail = exc.response.json().get("detail", detail)
            except Exception:
                pass
            return {"success": False, "error": str(detail), "data": [], "columns": []}
        except Exception as exc:
            return {"success": False, "error": str(exc), "data": [], "columns": []}

    # ── No connection: execute against uploaded files via DuckDB ────────
    try:
        import duckdb
        import pathlib
        from shared.data_utils import build_schema_context

        # Look in all upload directories
        base = pathlib.Path(__file__).resolve().parent.parent
        upload_dirs = [
            base / "data" / "uploads",   # file_service managed
            base / "api_gateway" / "uploads",  # legacy direct uploads
            base.parent / "uploads",      # root uploads dir
        ]

        con = duckdb.connect(":memory:")

        # Smart-load all files with header inference
        build_schema_context(con, upload_dirs, use_llm=True)

        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        records = [dict(zip(columns, row)) for row in rows]
        
        # Manually generate insight using the unified AnalysisAgent
        conclusion = None
        if records:
            from agents.specialists.analysis_agent import AnalysisAgent
            from agents.base import AgentContext
            agent = AnalysisAgent()
            ctx = AgentContext(
                user_prompt="Explain these executed SQL results conceptually.",
                task_description="Analyze the executed query results.",
                upstream_results={"t2": {"records": records}}
            )
            analysis_res = await agent.execute(ctx)
            if analysis_res.succeeded:
                conclusion = analysis_res.output.get("conclusion")

        elapsed = (time.time() - start) * 1000

        con.close()
        return {
            "success": True,
            "data": records,
            "columns": columns,
            "row_count": len(records),
            "execution_time_ms": round(elapsed, 1),
            "conclusion": conclusion,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": [], "columns": []}


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

        execution_time = (time.time() - start_time) * 1000

        # Generate insights natively via AnalysisAgent
        conclusion = None
        if results:
            from agents.specialists.analysis_agent import AnalysisAgent
            from agents.base import AgentContext
            agent = AnalysisAgent()
            ctx = AgentContext(
                user_prompt="Explain these results conceptually.",
                task_description="Analyze the executed query results.",
                upstream_results={"t2": {"records": results}}
            )
            analysis_res = await agent.execute(ctx)
            if analysis_res.succeeded:
                conclusion = analysis_res.output.get("conclusion")

        columns = list(results[0].keys()) if results else []

        return ExecuteQueryResponse(
            success=True,
            data=results,
            rows=len(results),
            columns=columns,
            insights={"conclusion": conclusion} if conclusion else None,
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
    session_id: Optional[str] = None
    uploaded_file: Optional[str] = None
    columns: Optional[List[str]] = None
    auto_execute: bool = True


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
    """Unified chat endpoint: NL → SQL → Execute → Visualize in one call.

    1. Discovers available tables from uploaded files (DuckDB) with smart header detection.
    2. Sends rich schema context (columns, types, sample data, relationships) to LLM.
    3. Auto-executes the SQL on DuckDB.
    4. Returns data + generated SQL + chart suggestion.
    """
    import time
    import duckdb
    import pathlib
    from shared.data_utils import build_schema_context

    t0 = time.time()
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    # ── Step 1: Smart-load all tables with header inference ─────────
    base = pathlib.Path(__file__).resolve().parent.parent
    upload_dirs = [
        base / "data" / "uploads",
        base / "api_gateway" / "uploads",
        base.parent / "uploads",
    ]

    con = duckdb.connect(":memory:")
    schema_result = build_schema_context(con, upload_dirs, use_llm=True)
    table_schemas = {
        name: [c["name"] for c in info["columns"]]
        for name, info in schema_result["tables"].items()
    }

    # Use the rich context string (includes types, samples, relationships)
    if table_schemas:
        schema_context = schema_result["context_text"]
    else:
        schema_context = "No tables available. User needs to upload a file first."

    # Merge with any explicit context from the request
    full_context = schema_context
    if request.context:
        full_context = f"{request.context}\n\n{schema_context}"
    if request.uploaded_file:
        full_context = f"Active file: {request.uploaded_file}\n{full_context}"
    if request.columns:
        full_context = f"Columns: {', '.join(request.columns)}\n{full_context}"

    session_id = request.session_id or f"chat_{int(time.time()*1000)}"

    # ── Step 2: Unified Agent DAG Pipeline ──────────────────────────
    from agents.base import AgentContext
    from agents.specialists.intent_agent import IntentAgent
    from agents.executor import DAGExecutor
    from agents.planner import ExecutionPlan, TaskNode, TaskType

    # 1. Check Intent First (Standalone early exit)
    intent_agent = IntentAgent()
    ctx = AgentContext(
        user_prompt=message,
        task_description="Determine intent.",
        schema_context=table_schemas
    )
    
    async def console_cb(agent_name: str, msg: str, pct: float):
        logger.info(f"[{agent_name}] {msg}")
        
    intent_agent.set_progress_callback(console_cb)
    intent_result = await intent_agent.execute(ctx)
    intent = intent_result.output.get("intent") if intent_result.succeeded else "sql"
    
    if intent == "conversation":
        con.close()
        return {
            "status": "Conversational",
            "job_id": f"job_{session_id}",
            "message": intent_result.output.get("message", "Hello! How can I help you today?"),
            "execution_time_ms": round((time.time() - t0) * 1000, 1),
            "available_tables": list(table_schemas.keys()),
        }

    # 2. Build explicit execution plan for SQL queries
    plan = ExecutionPlan(
        plan_id=session_id,
        user_prompt=message,
        summary="Unified SQL Pipeline",
        tasks=[
            TaskNode(id="t1", task_type=TaskType.GENERATE_SQL, description=f"Generate SQL to answer: {message}", agent_name="SQLGeneratorAgent", depends_on=[]),
            TaskNode(id="t2", task_type=TaskType.EXECUTE_SQL, description="Execute Query", agent_name="ExecutionAgent", depends_on=["t1"], parameters={"duckdb_con": con}),
            TaskNode(id="t3", task_type=TaskType.TRANSFORM, description="Suggest Chart", agent_name="VisualizationAgent", depends_on=["t2"]),
            TaskNode(id="t4", task_type=TaskType.TRANSFORM, description="Analyze Output", agent_name="AnalysisAgent", depends_on=["t2"]),
        ]
    )
    
    # 3. Execute the DAG!
    executor = DAGExecutor()
    executor.progress_cb = console_cb
    
    report = await executor.execute(
        plan, 
        user_prompt=message, 
        schema_context={"tables": table_schemas, "rich_context": full_context}
    )
    
    # Extract results from plan execution
    generated_sql = None
    gen_status = "Success" if report.success else "Error"
    error_message = report.summary if not report.success else None
    
    execution_result = {
        "success": False,
        "data": [],
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart_spec": None,
        "conclusion": None,
        "error": None
    }
    
    for task_id, task_result in report.task_results.items():
        if not task_result.succeeded and task_id == "t1":
            error_message = f"SQL Generation failed: {task_result.error}"
        if not task_result.succeeded and task_id == "t2":
            execution_result["error"] = f"Execution failed: {task_result.error}"
            
        task_output = task_result.output
        if "sql" in task_output and task_output["sql"]:
             generated_sql = task_output["sql"]
        if "records" in task_output:
             execution_result["success"] = True
             execution_result["data"] = task_output["records"]
             execution_result["columns"] = task_output["columns"]
             execution_result["rows"] = task_output["rows"]
             execution_result["row_count"] = len(task_output["records"])
        if "chart_spec" in task_output:
             execution_result["chart_spec"] = task_output["chart_spec"]
        if "conclusion" in task_output:
             execution_result["conclusion"] = task_output["conclusion"]
             
    con.close()
    elapsed_ms = (time.time() - t0) * 1000

    # ── Track query in server-side history ──────────────────────────
    if generated_sql:
        q_status = "success" if execution_result.get("success") else "error"
        q_rows = execution_result.get("row_count", 0)
        _track_query(message, generated_sql, q_status, q_rows, elapsed_ms)

    # ── Step 4: Build response ──────────────────────────────────────
    from datetime import datetime
    response = {
        "status": "Success" if error_message is None else "Error",
        "job_id": f"job_{session_id}",
        "final_query": generated_sql,
        "execution_time_ms": round(elapsed_ms, 1),
        "available_tables": list(table_schemas.keys()),
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "tables_loaded": len(table_schemas),
        },
    }

    if error_message:
        response["error_message"] = error_message
    if execution_result.get("success") or execution_result.get("error"):
        response["execution_result"] = execution_result

    return response


def _serialize_value(val: Any) -> Any:
    """Make DuckDB values JSON-serializable with faithful representation."""
    import decimal
    import math
    if val is None:
        return None
    if isinstance(val, decimal.Decimal):
        # Preserve exact decimal representation (e.g. 1234.56 stays 1234.56)
        if val == int(val):
            return int(val)
        return float(val)
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        # If the float is actually a whole number, show as int (e.g. 42.0 → 42)
        if val == int(val) and abs(val) < 2**53:
            return int(val)
        return round(val, 10)  # trim floating-point noise
    if isinstance(val, (datetime,)):
        return val.isoformat()
    if hasattr(val, 'isoformat'):
        return val.isoformat()
    return val


def _suggest_chart(user_query: str, columns: List[str], data: List[Dict]) -> Optional[Dict[str, Any]]:
    """Heuristic chart-type suggestion based on query keywords and result shape."""
    if not data or not columns:
        return None

    q = user_query.lower()
    num_cols = [c for c in columns if data and isinstance(data[0].get(c), (int, float))]
    str_cols = [c for c in columns if c not in num_cols]

    # Time-series keywords → line chart
    if any(w in q for w in ["trend", "over time", "monthly", "daily", "weekly", "yearly", "time series", "growth"]):
        x_col = str_cols[0] if str_cols else columns[0]
        y_col = num_cols[0] if num_cols else columns[-1]
        return {"type": "line", "x": x_col, "y": y_col, "title": "Trend"}

    # Distribution / comparison keywords → bar chart
    if any(w in q for w in ["top", "bottom", "rank", "compare", "by category", "per", "group", "breakdown"]):
        x_col = str_cols[0] if str_cols else columns[0]
        y_col = num_cols[0] if num_cols else columns[-1]
        return {"type": "bar", "x": x_col, "y": y_col, "title": "Comparison"}

    # Proportion keywords → pie chart
    if any(w in q for w in ["distribution", "share", "percentage", "proportion", "pie", "breakdown"]):
        x_col = str_cols[0] if str_cols else columns[0]
        y_col = num_cols[0] if num_cols else columns[-1]
        return {"type": "pie", "x": x_col, "y": y_col, "title": "Distribution"}

    # Default: if 1 string + 1 numeric column → bar chart
    if len(str_cols) >= 1 and len(num_cols) >= 1:
        return {"type": "bar", "x": str_cols[0], "y": num_cols[0], "title": "Results"}

    # Scalar result (1 row, 1-2 columns) → no chart
    if len(data) <= 1:
        return None

    # Multiple numeric columns → table (no chart)
    return None


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
        db_svc = os.getenv("DATABASE_SERVICE_URL", "http://localhost:8002")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{db_svc}/databases/test/{db_type}")
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
        # 2. Create the folder safely — save alongside data/uploads so /chat can find them
        upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        
        # 3. Save the file
        file_path = os.path.join(upload_dir, target_file.filename)
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


# ==================== In-Memory Enterprise Stores ====================
# Thread-safe stores for connections, query history, and dashboard metrics.
# Production would use PostgreSQL/Redis; these provide full API contracts now.

import threading
import uuid as _uuid

_connections_lock = threading.Lock()
_connections_store: Dict[str, Dict[str, Any]] = {}  # id → connection dict

_query_history_lock = threading.Lock()
_query_history_store: List[Dict[str, Any]] = []  # newest-first

_dashboard_counters_lock = threading.Lock()
_dashboard_counters: Dict[str, int] = {"total_rows": 0, "queries_run": 0}

_jobs_lock = threading.Lock()
_jobs_store: Dict[str, Dict[str, Any]] = {}  # job_id → job dict


def _track_query(prompt: str, sql: str, status: str, rows: int, execution_time_ms: float):
    """Record a query execution in the in-memory store."""
    record = {
        "id": f"q_{int(datetime.now().timestamp() * 1000)}",
        "prompt": prompt,
        "sql": sql,
        "status": status,
        "rows": rows,
        "executionTime": round(execution_time_ms, 1),
        "timestamp": datetime.now().isoformat(),
    }
    with _query_history_lock:
        _query_history_store.insert(0, record)
        # Keep last 200 entries
        if len(_query_history_store) > 200:
            del _query_history_store[200:]
    with _dashboard_counters_lock:
        _dashboard_counters["queries_run"] += 1
        if status == "success":
            _dashboard_counters["total_rows"] += rows


# ==================== Connections CRUD ====================

class ConnectionCreateRequest(BaseModel):
    name: str
    type: str  # postgresql, mysql, bigquery, sqlite, csv, duckdb
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl: bool = False
    extra: Dict[str, Any] = Field(default_factory=dict)


@app.get("/connections")
async def get_connections():
    """List all registered data source connections."""
    with _connections_lock:
        conns = list(_connections_store.values())
    # Also count uploaded files as implicit file sources
    file_sources = 0
    try:
        base = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = base / "data" / "uploads"
        if upload_dir.exists():
            file_sources = len([f for f in upload_dir.iterdir() if f.is_file()])
    except Exception:
        pass
    return {
        "success": True,
        "connections": conns,
        "count": len(conns),
        "file_sources": file_sources,
    }


@app.post("/connections")
async def create_connection(req: ConnectionCreateRequest):
    """Register a new data source connection."""
    conn_id = str(_uuid.uuid4())[:12]
    now = datetime.now().isoformat()
    conn = {
        "id": conn_id,
        "name": req.name,
        "type": req.type,
        "host": req.host,
        "port": req.port,
        "database": req.database,
        "username": req.username,
        "ssl": req.ssl,
        "is_active": False,
        "created_at": now,
        "updated_at": now,
        "last_tested": None,
        "table_count": 0,
    }
    with _connections_lock:
        _connections_store[conn_id] = conn
    logger.info("Connection created: %s (%s/%s)", conn_id, req.type, req.name)
    return {"success": True, "connection": conn}


@app.post("/connections/{connection_id}/test")
async def test_connection_by_id(connection_id: str):
    """Test an existing connection by ID."""
    with _connections_lock:
        conn = _connections_store.get(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(conn["type"]),
            name=conn["name"],
            host=conn.get("host", ""),
            port=conn.get("port", 5432),
            username=conn.get("username", ""),
            password="",  # never stored — user re-supplies for test
            database=conn.get("database", ""),
        )
        if conn["type"] == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif conn["type"] == "mysql":
            connector = MySQLConnector(connector_config)
        elif conn["type"] == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            return {"success": True, "message": f"Connection type '{conn['type']}' registered (test skipped)"}

        connected = await connector.connect()
        if connected:
            tables = await connector.list_tables()
            await connector.disconnect()
            with _connections_lock:
                _connections_store[connection_id]["is_active"] = True
                _connections_store[connection_id]["last_tested"] = datetime.now().isoformat()
                _connections_store[connection_id]["table_count"] = len(tables)
            return {"success": True, "message": f"Connected. Found {len(tables)} tables.", "table_count": len(tables)}
        return {"success": False, "message": "Connection failed"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str):
    """Remove a registered connection."""
    with _connections_lock:
        removed = _connections_store.pop(connection_id, None)
    if not removed:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"success": True, "message": f"Connection '{removed['name']}' deleted"}


@app.get("/connections/{connection_id}/schema")
async def get_connection_schema(connection_id: str):
    """Get table/column schema for a connection."""
    with _connections_lock:
        conn = _connections_store.get(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(conn["type"]),
            name=conn["name"],
            host=conn.get("host", ""),
            port=conn.get("port", 5432),
            username=conn.get("username", ""),
            password="",
            database=conn.get("database", ""),
        )
        if conn["type"] == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif conn["type"] == "mysql":
            connector = MySQLConnector(connector_config)
        elif conn["type"] == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            return {"success": True, "schema": {}, "message": "Schema introspection not available for this type"}

        await connector.connect()
        tables = await connector.list_tables()
        schema: Dict[str, List[str]] = {}
        for t in tables[:50]:  # Limit to 50 tables for performance
            try:
                profile = await connector.profile_table(t)
                schema[t] = list(profile.get("columns", {}).keys()) if isinstance(profile.get("columns"), dict) else []
            except Exception:
                schema[t] = []
        await connector.disconnect()
        return {"success": True, "schema": schema, "table_count": len(tables)}
    except Exception as e:
        return {"success": False, "error": str(e), "schema": {}}


# ==================== Query History ====================

@app.get("/query-history")
async def get_query_history(limit: int = 50, status_filter: Optional[str] = None):
    """Get server-side query history."""
    with _query_history_lock:
        records = list(_query_history_store)
    if status_filter and status_filter != "all":
        records = [r for r in records if r.get("status") == status_filter]
    return {"success": True, "queries": records[:limit], "total": len(records)}


@app.post("/query-history")
async def save_query_history(payload: Dict[str, Any]):
    """Save a query execution record (called by frontend as backup)."""
    record = {
        "id": payload.get("id", f"q_{int(datetime.now().timestamp() * 1000)}"),
        "prompt": payload.get("prompt", ""),
        "sql": payload.get("sql", ""),
        "status": payload.get("status", "success"),
        "rows": payload.get("rows", 0),
        "executionTime": payload.get("executionTime", 0),
        "timestamp": payload.get("timestamp", datetime.now().isoformat()),
    }
    with _query_history_lock:
        _query_history_store.insert(0, record)
        if len(_query_history_store) > 200:
            del _query_history_store[200:]
    return {"success": True, "id": record["id"]}


# ==================== Dashboard Stats ====================

@app.get("/dashboard/stats")
async def get_dashboard_stats():
    """Real-time dashboard statistics."""
    # Count file sources
    file_count = 0
    total_file_rows = 0
    try:
        base = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = base / "data" / "uploads"
        if upload_dir.exists():
            import duckdb
            for f in upload_dir.iterdir():
                if f.is_file() and f.suffix.lower() in (".csv", ".json", ".parquet", ".xlsx", ".xls"):
                    file_count += 1
                    try:
                        con = duckdb.connect(":memory:")
                        if f.suffix.lower() == ".csv":
                            count = con.execute(f"SELECT COUNT(*) FROM read_csv_auto('{str(f).replace(chr(92), '/')}')").fetchone()[0]
                        elif f.suffix.lower() == ".parquet":
                            count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{str(f).replace(chr(92), '/')}')").fetchone()[0]
                        elif f.suffix.lower() == ".json":
                            count = con.execute(f"SELECT COUNT(*) FROM read_json_auto('{str(f).replace(chr(92), '/')}')").fetchone()[0]
                        else:
                            count = 0
                        total_file_rows += count
                        con.close()
                    except Exception:
                        pass
    except Exception:
        pass

    with _connections_lock:
        active_conns = sum(1 for c in _connections_store.values() if c.get("is_active"))
        total_conns = len(_connections_store)
    with _dashboard_counters_lock:
        queries_run = _dashboard_counters["queries_run"]
        tracked_rows = _dashboard_counters["total_rows"]

    return {
        "total_rows": total_file_rows + tracked_rows,
        "active_sources": file_count + active_conns,
        "total_connections": total_conns,
        "file_sources": file_count,
        "queries_run": queries_run,
        "system_health": "healthy",
        "uptime_percentage": 99.9,
    }


# ==================== Job Control ====================

@app.post("/jobs/{job_id}/approve")
async def approve_job(job_id: str):
    """Approve a pending job for execution."""
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        if job:
            job["status"] = "approved"
            job["approved_at"] = datetime.now().isoformat()
            return {"success": True, "job_id": job_id, "status": "approved"}
    # If not in store, treat as auto-approved
    return {"success": True, "job_id": job_id, "status": "approved", "message": "Job approved (auto)"}


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a pending or running job."""
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        if job:
            job["status"] = "cancelled"
            job["cancelled_at"] = datetime.now().isoformat()
            return {"success": True, "job_id": job_id, "status": "cancelled"}
    return {"success": True, "job_id": job_id, "status": "cancelled", "message": "Job cancelled"}


# ==================== Chat History ====================

_chat_history_lock = threading.Lock()
_chat_history_store: Dict[str, List[Dict[str, Any]]] = {}  # session_id → messages


@app.get("/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """Get chat history for a session."""
    with _chat_history_lock:
        messages = _chat_history_store.get(session_id, [])
    return messages


@app.post("/chat/history/{session_id}")
async def save_chat_message(session_id: str, payload: Dict[str, Any]):
    """Save a chat message to session history."""
    msg = {
        "id": payload.get("id", str(_uuid.uuid4())[:12]),
        "type": payload.get("type", "user"),
        "content": payload.get("content", ""),
        "timestamp": payload.get("timestamp", datetime.now().isoformat()),
        "metadata": payload.get("metadata"),
    }
    with _chat_history_lock:
        if session_id not in _chat_history_store:
            _chat_history_store[session_id] = []
        _chat_history_store[session_id].append(msg)
        # Keep last 100 messages per session
        if len(_chat_history_store[session_id]) > 100:
            _chat_history_store[session_id] = _chat_history_store[session_id][-100:]
    return {"success": True, "id": msg["id"]}


# ==================== ETL Pipeline ====================

class ETLTransformStep(BaseModel):
    """A single transform step in an ETL pipeline."""
    id: str = ""
    type: str = Field(..., description="Transform type: filter, rename, drop_columns, add_column, sort, aggregate, deduplicate, cast_type, fill_missing, custom_sql")
    description: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)

class ETLPipelineRequest(BaseModel):
    """Request to create & execute an ETL pipeline."""
    name: str = "Untitled Pipeline"
    source_file: str = Field(..., description="Uploaded filename to use as source")
    destination_format: str = Field(default="csv", description="Output format: csv, parquet, json")
    destination_filename: Optional[str] = None
    transforms: List[ETLTransformStep] = Field(default_factory=list)
    preview_only: bool = False  # True = return first 50 rows, False = write file

class ETLNaturalLanguageRequest(BaseModel):
    """Request to build an ETL pipeline from a natural language description."""
    source_file: str
    instruction: str
    destination_format: str = "csv"


def _q(name: str) -> str:
    """Double-quote a SQL identifier to handle special characters like & in table names."""
    return '"' + name.replace('"', '""') + '"'


def _build_transform_sql(table: str, steps: List[ETLTransformStep], con=None) -> str:
    """Convert a list of transform steps into a single DuckDB SQL pipeline.

    Guards against empty / misconfigured steps — skips any step that cannot
    produce valid SQL rather than generating broken queries.

    Pass *con* (a DuckDB connection) to enable column-wildcard features like
    ``fill_missing`` with ``column: "*"``.
    """
    if not steps:
        return f"SELECT * FROM {_q(table)}"

    cte_parts: List[str] = []
    prev = table
    skipped = 0

    for i, step in enumerate(steps):
        alias = f"step_{i}"
        cfg = step.config or {}
        t = step.type

        if t == "filter":
            condition = (cfg.get("condition") or "").strip()
            if not condition:
                logger.warning("ETL step %d (filter): empty condition — skipping", i)
                skipped += 1
                continue
            cte_parts.append(f"{alias} AS (SELECT * FROM {_q(prev)} WHERE {condition})")

        elif t == "rename":
            mappings = cfg.get("mappings") or {}
            valid = {old: new for old, new in mappings.items() if old and new}
            if not valid:
                logger.warning("ETL step %d (rename): no valid mappings — skipping", i)
                skipped += 1
                continue
            renames = [f'"{old}" AS "{new}"' for old, new in valid.items()]
            cte_parts.append(f'{alias} AS (SELECT * RENAME ({", ".join(renames)}) FROM {_q(prev)})')

        elif t == "drop_columns":
            cols = [c for c in (cfg.get("columns") or []) if c]
            if not cols:
                logger.warning("ETL step %d (drop_columns): no columns — skipping", i)
                skipped += 1
                continue
            excludes = ", ".join(f'"{c}"' for c in cols)
            cte_parts.append(f"{alias} AS (SELECT * EXCLUDE ({excludes}) FROM {_q(prev)})")

        elif t == "add_column":
            expr = (cfg.get("expression") or "").strip()
            col_name = (cfg.get("name") or "").strip()
            if not expr or not col_name:
                logger.warning("ETL step %d (add_column): missing name/expression — skipping", i)
                skipped += 1
                continue
            cte_parts.append(f'{alias} AS (SELECT *, ({expr}) AS "{col_name}" FROM {_q(prev)})')

        elif t == "sort":
            col = (cfg.get("column") or "").strip()
            if not col:
                logger.warning("ETL step %d (sort): no column — skipping", i)
                skipped += 1
                continue
            order = cfg.get("order", "ASC").upper()
            if order not in ("ASC", "DESC"):
                order = "ASC"
            cte_parts.append(f'{alias} AS (SELECT * FROM {_q(prev)} ORDER BY "{col}" {order})')

        elif t == "aggregate":
            group_by = [c for c in (cfg.get("group_by") or []) if c]
            agg_exprs = cfg.get("aggregations") or []
            valid_aggs = [a for a in agg_exprs if a.get("column") and a.get("func")]
            if not group_by or not valid_aggs:
                logger.warning("ETL step %d (aggregate): missing group_by/aggregations — skipping", i)
                skipped += 1
                continue
            g = ", ".join(f'"{c}"' for c in group_by)
            a = ", ".join(
                f'{agg["func"]}("{agg["column"]}") AS "{agg.get("alias", agg["column"])}"'
                for agg in valid_aggs
            )
            cte_parts.append(f"{alias} AS (SELECT {g}, {a} FROM {_q(prev)} GROUP BY {g})")

        elif t == "deduplicate":
            cols = [c for c in (cfg.get("columns") or []) if c]
            if cols:
                partition = ", ".join(f'"{c}"' for c in cols)
                cte_parts.append(
                    f"{alias} AS (SELECT * FROM (SELECT *, ROW_NUMBER() OVER "
                    f"(PARTITION BY {partition}) AS _rn FROM {_q(prev)}) WHERE _rn = 1)"
                )
            else:
                cte_parts.append(f"{alias} AS (SELECT DISTINCT * FROM {_q(prev)})")

        elif t == "cast_type":
            col = (cfg.get("column") or "").strip()
            to_type = (cfg.get("to_type") or "").strip()
            if not col or not to_type:
                logger.warning("ETL step %d (cast_type): missing column/type — skipping", i)
                skipped += 1
                continue
            cte_parts.append(
                f'{alias} AS (SELECT * REPLACE (CAST("{col}" AS {to_type}) AS "{col}") FROM {_q(prev)})'
            )

        elif t == "fill_missing":
            col = (cfg.get("column") or "").strip()
            fill_val = (cfg.get("value") or "").strip()
            strategy = (cfg.get("strategy") or "value").strip().lower()

            # ── Fill ALL columns when column is "*" ──
            if col == "*" and con is not None:
                src = _q(prev) if cte_parts else _q(table)
                try:
                    schema = con.execute(f'DESCRIBE (SELECT * FROM {src})').fetchall() if cte_parts else con.execute(f'DESCRIBE {_q(table)}').fetchall()
                except Exception:
                    schema = con.execute(f'DESCRIBE {_q(table)}').fetchall()

                # Detect whether the user's fill_val looks numeric
                _val_is_numeric = False
                if fill_val:
                    try:
                        float(fill_val)
                        _val_is_numeric = True
                    except ValueError:
                        pass

                # ── Optimisation: only touch columns that actually have NULLs ──
                try:
                    null_checks = " OR ".join(f'"{c}" IS NULL' for c, *_ in schema)
                    null_count_exprs = ", ".join(
                        f'SUM(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END) AS "{c}"'
                        for c, *_ in schema
                    )
                    null_row = con.execute(
                        f'SELECT {null_count_exprs} FROM {src}'
                    ).fetchone()
                    cols_with_nulls = {schema[j][0] for j, cnt in enumerate(null_row) if cnt and cnt > 0}
                except Exception:
                    cols_with_nulls = None  # fallback: fill all

                replaces = []
                for c_name, c_type, *_ in schema:
                    # Skip columns that have no NULLs (when we could detect)
                    if cols_with_nulls is not None and c_name not in cols_with_nulls:
                        continue
                    is_numeric = any(t in c_type.upper() for t in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "BIGINT", "SMALLINT", "TINYINT", "REAL"))
                    if strategy == "mean" and is_numeric:
                        replaces.append(f'COALESCE("{c_name}", AVG("{c_name}") OVER ()) AS "{c_name}"')
                    elif strategy == "median" and is_numeric:
                        replaces.append(f'COALESCE("{c_name}", MEDIAN("{c_name}") OVER ()) AS "{c_name}"')
                    elif strategy in ("mean", "median") and not is_numeric:
                        # text columns: use text fallback or leave as-is
                        if fill_val and not _val_is_numeric:
                            safe = fill_val.replace("'", "''")
                            replaces.append(f"COALESCE(\"{c_name}\", '{safe}') AS \"{c_name}\"")
                        # else: skip text columns for mean/median — no sensible fill
                    elif is_numeric and fill_val:
                        replaces.append(f'COALESCE("{c_name}", {fill_val}) AS "{c_name}"')
                    elif is_numeric and not fill_val:
                        replaces.append(f'COALESCE("{c_name}", 0) AS "{c_name}"')
                    elif not is_numeric and fill_val and not _val_is_numeric:
                        # User gave a text value — apply to text columns
                        safe = fill_val.replace("'", "''")
                        replaces.append(f"COALESCE(\"{c_name}\", '{safe}') AS \"{c_name}\"")
                    # else: text column + numeric fill value → skip (don't fill text cols with numbers)
                if replaces:
                    cte_parts.append(f'{alias} AS (SELECT * REPLACE ({", ".join(replaces)}) FROM {_q(prev)})')
                else:
                    skipped += 1
                    continue
            elif not col or not fill_val:
                logger.warning("ETL step %d (fill_missing): missing column/value — skipping", i)
                skipped += 1
                continue
            else:
                cte_parts.append(
                    f'{alias} AS (SELECT * REPLACE (COALESCE("{col}", {fill_val}) AS "{col}") FROM {_q(prev)})'
                )

        elif t == "custom_sql":
            sql_expr = (cfg.get("sql") or "").strip()
            if not sql_expr:
                logger.warning("ETL step %d (custom_sql): empty sql — skipping", i)
                skipped += 1
                continue
            sql_expr = sql_expr.replace("{{input}}", _q(prev))
            cte_parts.append(f"{alias} AS ({sql_expr})")

        else:
            logger.warning("ETL step %d: unknown type '%s' — skipping", i, t)
            skipped += 1
            continue

        prev = alias

    if skipped:
        logger.info("ETL pipeline: %d step(s) skipped due to empty config", skipped)

    if cte_parts:
        return "WITH " + ",\n".join(cte_parts) + f"\nSELECT * FROM {_q(prev)}"
    return f"SELECT * FROM {_q(table)}"


@app.post("/etl/preview-source")
async def etl_preview_source(payload: Dict[str, Any]):
    """Preview the schema + first N rows of a source file for ETL configuration."""
    import duckdb
    from shared.data_utils import smart_load_file

    source_file = payload.get("source_file", "")
    limit = payload.get("limit", 20)
    base = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    upload_dirs = [base / "data" / "uploads"]

    file_path = None
    for d in upload_dirs:
        candidate = d / source_file
        if candidate.exists():
            file_path = str(candidate)
            break

    if not file_path:
        raise HTTPException(status_code=404, detail=f"Source file '{source_file}' not found in uploads")

    try:
        con = duckdb.connect(":memory:")
        table_name = re.sub(r"[^A-Za-z0-9_]", "_", Path(source_file).stem)
        file_info = smart_load_file(con, file_path, table_name, use_llm=True)

        columns = file_info["columns"]
        row_count = file_info["row_count"]
        col_names = [c["name"] for c in columns]

        # Get preview rows from the typed table (smart_load_file already loaded it)
        preview = con.execute(f'SELECT * FROM "{table_name}" LIMIT {limit}').fetchall()
        preview_records = [
            {col: _serialize_value(val) for col, val in zip(col_names, row)}
            for row in preview
        ]

        con.close()
        return {
            "status": "success",
            "source_file": source_file,
            "table_name": table_name,
            "columns": columns,
            "row_count": row_count,
            "preview": preview_records,
            "headers_inferred": file_info.get("headers_inferred", False),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/etl/execute")
async def etl_execute(pipeline: ETLPipelineRequest):
    """Execute an ETL pipeline: load source → apply transforms → write destination."""
    import duckdb
    import time as _time
    from shared.data_utils import smart_load_file

    logger.info(
        "ETL execute: pipeline='%s' source='%s' transforms=%d preview_only=%s",
        pipeline.name, pipeline.source_file, len(pipeline.transforms), pipeline.preview_only,
    )

    t0 = _time.time()
    base = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    upload_dir = base / "data" / "uploads"
    output_dir = base / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find source file
    file_path = upload_dir / pipeline.source_file
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Source file '{pipeline.source_file}' not found")

    try:
        con = duckdb.connect(":memory:")
        table_name = re.sub(r"[^A-Za-z0-9_]", "_", Path(pipeline.source_file).stem)

        # Extract: Smart-load source file with header inference
        file_info = smart_load_file(con, str(file_path), table_name, use_llm=True)

        # Get source stats
        source_count = file_info["row_count"]
        source_columns = file_info["columns"]

        # Transform: Build and execute SQL pipeline
        transform_sql = _build_transform_sql(table_name, pipeline.transforms, con=con)
        con.execute(f"CREATE TABLE _etl_output AS {transform_sql}")

        # Get output stats
        output_count = con.execute("SELECT COUNT(*) FROM _etl_output").fetchone()[0]
        output_schema = con.execute("DESCRIBE _etl_output").fetchall()
        output_columns = [{"name": r[0], "type": r[1]} for r in output_schema]

        # Preview (always return first 50 rows)
        preview_result = con.execute("SELECT * FROM _etl_output LIMIT 50").fetchall()
        col_names = [c["name"] for c in output_columns]
        preview_records = [
            {col: _serialize_value(val) for col, val in zip(col_names, row)}
            for row in preview_result
        ]

        output_path = None
        download_filename = None

        if not pipeline.preview_only:
            # Load: Write to destination file
            raw_dest = pipeline.destination_filename or f"{table_name}_transformed"
            # Strip any existing extension to avoid double extensions (e.g. "out.csv.csv")
            dest_name = Path(raw_dest).stem
            fmt = pipeline.destination_format.lower()

            if fmt == "csv":
                download_filename = f"{dest_name}.csv"
                output_path = str(output_dir / download_filename)
                con.execute(f"COPY _etl_output TO '{output_path}' (HEADER, DELIMITER ',')")
            elif fmt == "parquet":
                download_filename = f"{dest_name}.parquet"
                output_path = str(output_dir / download_filename)
                con.execute(f"COPY _etl_output TO '{output_path}' (FORMAT PARQUET)")
            elif fmt == "json":
                download_filename = f"{dest_name}.json"
                output_path = str(output_dir / download_filename)
                con.execute(f"COPY _etl_output TO '{output_path}' (FORMAT JSON, ARRAY true)")
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported destination format: {fmt}")

        con.close()
        elapsed_ms = (_time.time() - t0) * 1000
        logger.info(
            "ETL success: '%s' — %d→%d rows, %d transforms, %.1fms, file=%s",
            pipeline.name, source_count, output_count,
            len(pipeline.transforms), elapsed_ms, download_filename,
        )

        return {
            "status": "success",
            "pipeline_name": pipeline.name,
            "source": {
                "file": pipeline.source_file,
                "row_count": source_count,
                "columns": source_columns,
            },
            "output": {
                "row_count": output_count,
                "columns": output_columns,
                "file": download_filename,
                "format": pipeline.destination_format,
            },
            "transform_sql": transform_sql,
            "transforms_applied": len(pipeline.transforms),
            "preview": preview_records,
            "execution_time_ms": round(elapsed_ms, 1),
            "preview_only": pipeline.preview_only,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("ETL execute failed for '%s': %s", pipeline.name, e, exc_info=True)
        return {"status": "error", "error": str(e), "transform_sql": "", "preview": []}


@app.get("/etl/download/{filename}")
async def etl_download(filename: str):
    """Download a processed ETL output file."""
    from fastapi.responses import FileResponse

    base = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    output_dir = base / "data" / "processed"
    file_path = output_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Output file '{filename}' not found")

    media_types = {
        ".csv": "text/csv",
        ".parquet": "application/octet-stream",
        ".json": "application/json",
    }
    media_type = media_types.get(file_path.suffix.lower(), "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )


@app.post("/etl/natural-language")
async def etl_from_natural_language(req: ETLNaturalLanguageRequest):
    """Use LLM to build transform steps from a natural language instruction, then execute."""
    import duckdb
    from shared.data_utils import smart_load_file

    logger.info(
        "ETL NL: source='%s' instruction='%s' format='%s'",
        req.source_file, req.instruction[:80], req.destination_format,
    )

    # 1. Get source file schema using smart loader
    base = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    file_path = base / "data" / "uploads" / req.source_file
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Source file '{req.source_file}' not found")

    try:
        con = duckdb.connect(":memory:")
        table_name = re.sub(r"[^A-Za-z0-9_]", "_", Path(req.source_file).stem)
        file_info = smart_load_file(con, str(file_path), table_name, use_llm=True)

        schema_rows = [(c["name"], c["type"]) for c in file_info["columns"]]
        col_names = [c["name"] for c in file_info["columns"]]
        sample = con.execute(f'SELECT * FROM "{table_name}" LIMIT 5').fetchall()
        sample_records = [dict(zip(col_names, row)) for row in sample]
        con.close()

        schema_text = ", ".join(f"{r[0]} ({r[1]})" for r in schema_rows)
    except Exception as e:
        return {"status": "error", "error": f"Failed to read source: {e}", "transforms": []}

    # 2. Ask LLM to generate transform steps
    from shared.llm_provider import get_llm
    llm = get_llm()

    prompt = f"""You are a data transformation expert. Given a source table schema and user instruction,
generate a list of ETL transform steps as JSON.

SOURCE TABLE: {table_name}
COLUMNS: {schema_text}
SAMPLE DATA (first 5 rows): {json.dumps(sample_records[:3], default=str)}

USER INSTRUCTION: {req.instruction}

IMPORTANT: Always enclose ALL table and column names in double quotes (e.g., "my_table"."my_column") to ensure compatibility with identifiers containing special characters like '&', spaces, or reserved keywords.

Return ONLY a JSON array of transform step objects. Each step has:
- "type": one of "filter", "rename", "drop_columns", "add_column", "sort", "aggregate", "deduplicate", "cast_type", "fill_missing", "custom_sql"
- "description": what this step does
- "config": configuration object specific to the step type

Step config examples:
- filter: {{"condition": "price > 10"}}
- rename: {{"mappings": {{"old_col": "new_col"}}}}
- drop_columns: {{"columns": ["col1", "col2"]}}
- add_column: {{"name": "total", "expression": "price * quantity"}}
- sort: {{"column": "price", "order": "DESC"}}
- aggregate: {{"group_by": ["category"], "aggregations": [{{"column": "price", "func": "AVG", "alias": "avg_price"}}]}}
- deduplicate: {{"columns": ["id"]}}
- cast_type: {{"column": "price", "to_type": "DOUBLE"}}
- fill_missing: {{"column": "name", "value": "'Unknown'"}}
- custom_sql: {{"sql": "SELECT *, price * 1.1 AS price_with_tax FROM {{{{input}}}}"}}

Return ONLY the JSON array, no markdown, no explanation."""

    transforms = []
    llm_error = None
    if llm.is_available():
        try:
            logger.info("ETL NL: calling LLM (%s)…", llm)
            parsed = llm.generate_json(prompt)
            logger.info("ETL NL: LLM returned type=%s", type(parsed).__name__)
            if isinstance(parsed, list):
                transforms = parsed
                logger.info("ETL NL: generated %d transform steps", len(transforms))
            elif isinstance(parsed, dict) and "transforms" in parsed:
                transforms = parsed["transforms"]
                logger.info("ETL NL: extracted %d steps from wrapper", len(transforms))
            else:
                logger.warning("ETL NL: LLM returned unexpected format: %s", str(parsed)[:200])
        except Exception as e:
            llm_error = str(e)
            logger.error("ETL NL: LLM call failed: %s", e, exc_info=True)
    else:
        llm_error = "No LLM provider available"
        logger.warning("ETL NL: no LLM provider available")

    if not transforms:
        if llm_error:
            return {
                "status": "error",
                "error": f"LLM failed: {llm_error}",
                "source_file": req.source_file,
                "instruction": req.instruction,
                "transforms": [],
                "schema": [{"name": r[0], "type": r[1]} for r in schema_rows],
            }
        # Fallback: create a simple custom_sql step from the instruction
        transforms = [{
            "type": "custom_sql",
            "description": req.instruction,
            "config": {"sql": f"SELECT * FROM {{{{input}}}}"},
        }]

    return {
        "status": "success",
        "source_file": req.source_file,
        "instruction": req.instruction,
        "transforms": transforms,
        "schema": [{"name": r[0], "type": r[1]} for r in schema_rows],
    }


# ── Health is provided by create_service() ──


# ═══════════════════════════════════════════════════════════════════
# Pipeline API  —  AI-driven Source → Process → Sink Pipelines
# ═══════════════════════════════════════════════════════════════════

from pipeline.engine import PipelineEngine
from pipeline.generator import PipelineGenerator
from pipeline.models import (
    Pipeline as PipelineModel,
    PipelineRun as PipelineRunModel,
    PipelineSource as PipelineSourceModel,
    PipelineSink as PipelineSinkModel,
    PipelineStatus as PipelineStatusEnum,
)

_pipeline_engine = PipelineEngine()
_pipeline_generator: Optional[PipelineGenerator] = None


def _get_generator() -> PipelineGenerator:
    global _pipeline_generator
    if _pipeline_generator is None:
        _pipeline_generator = PipelineGenerator()
    return _pipeline_generator


# ── Request / Response models ─────────────────────────────────────

class PipelineGenerateRequest(BaseModel):
    prompt: str
    source_file: Optional[str] = None
    include_schema: bool = True

class PipelineExecuteRequest(BaseModel):
    pipeline: Dict[str, Any]
    preview_only: bool = False

class PipelineSaveRequest(BaseModel):
    pipeline: Dict[str, Any]


# ── Generate pipeline from NL prompt ──────────────────────────────

@app.post("/pipeline/generate")
async def pipeline_generate(req: PipelineGenerateRequest):
    """Convert a natural language prompt into a Pipeline definition."""
    logger.info("[Pipeline] Generate request: %s", req.prompt[:200])

    gen = _get_generator()

    # Build schema context if requested
    schema_context = None
    available_files = None

    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
    skip = {".gitkeep", ".DS_Store"}
    data_exts = {".csv", ".parquet", ".json", ".xlsx", ".tsv"}
    if os.path.isdir(upload_dir):
        available_files = [
            f for f in sorted(os.listdir(upload_dir))
            if f not in skip and os.path.splitext(f)[1].lower() in data_exts
        ]

    if req.include_schema:
        schema_context = {}
        target_file = req.source_file
        if not target_file and available_files:
            target_file = available_files[0]
        if target_file:
            try:
                schema_context[target_file] = gen.get_file_schema(target_file)
            except Exception as e:
                logger.warning("[Pipeline] Schema read failed for %s: %s", target_file, e)

    try:
        pipeline = await gen.generate(
            prompt=req.prompt,
            available_files=available_files,
            schema_context=schema_context,
        )
        # If user specified a source file, override
        if req.source_file and pipeline.source.type.value == "file":
            pipeline.source.file_name = req.source_file

        return {
            "status": "success",
            "pipeline": pipeline.model_dump(),
        }
    except Exception as e:
        logger.error("[Pipeline] Generate failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# ── Execute a pipeline ────────────────────────────────────────────

@app.post("/pipeline/execute")
async def pipeline_execute(req: PipelineExecuteRequest):
    """Execute a pipeline definition and return results."""
    logger.info("[Pipeline] Execute request (preview=%s)", req.preview_only)

    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid pipeline: {e}")

    try:
        run = await _pipeline_engine.execute(
            pipeline, preview_only=req.preview_only
        )
        return {
            "status": "success",
            "run": run.model_dump(),
        }
    except Exception as e:
        logger.error("[Pipeline] Execute failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# ── Save pipeline ─────────────────────────────────────────────────

@app.post("/pipeline/save")
async def pipeline_save(req: PipelineSaveRequest):
    """Save a pipeline definition for later use."""
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid pipeline: {e}")

    saved = _pipeline_engine.save(pipeline)
    return {"status": "success", "pipeline_id": saved.id, "name": saved.name}


# ── List saved pipelines ──────────────────────────────────────────

@app.get("/pipeline/list")
async def pipeline_list():
    """List all saved pipelines."""
    pipelines = _pipeline_engine.list_all()
    return {
        "status": "success",
        "count": len(pipelines),
        "pipelines": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "source": p.source.label(),
                "steps": len(p.steps),
                "sink": p.sink.type.value,
                "status": p.status.value,
                "created_at": p.created_at,
                "tags": p.tags,
            }
            for p in pipelines
        ],
    }


# ── Get pipeline details ──────────────────────────────────────────

@app.get("/pipeline/{pipeline_id}")
async def pipeline_get(pipeline_id: str):
    """Get a saved pipeline by ID."""
    p = _pipeline_engine.get(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "pipeline": p.model_dump()}


# ── Delete pipeline ───────────────────────────────────────────────

@app.delete("/pipeline/{pipeline_id}")
async def pipeline_delete(pipeline_id: str):
    """Delete a saved pipeline."""
    deleted = _pipeline_engine.delete(pipeline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "deleted": pipeline_id}


# ── Get file schema (for pipeline builder context) ────────────────

@app.get("/pipeline/schema/{file_name}")
async def pipeline_file_schema(file_name: str):
    """Get column schema for a file (used by pipeline builder UI)."""
    gen = _get_generator()
    try:
        schema = gen.get_file_schema(file_name)
        return {"status": "success", "schema": schema}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Download pipeline output ──────────────────────────────────────

@app.get("/pipeline/download/{filename}")
async def pipeline_download(filename: str):
    """Download a pipeline output file."""
    from fastapi.responses import FileResponse
    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "processed")
    file_path = os.path.join(output_dir, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(file_path, filename=filename)


# ── UASR Self-Healing proxy routes ────────────────────────────────

_UASR_URL = os.getenv("AURA_UASR_URL", "http://localhost:8009")


@app.post("/uasr/ingest")
async def uasr_ingest(req: Dict[str, Any]):
    """Proxy: submit batch for drift detection & recovery."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{_UASR_URL}/uasr/ingest", json=req)
        return resp.json()


@app.post("/uasr/baseline")
async def uasr_baseline(req: Dict[str, Any]):
    """Proxy: register a reference baseline."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{_UASR_URL}/uasr/baseline", json=req)
        return resp.json()


@app.get("/uasr/metrics")
async def uasr_metrics():
    """Proxy: Hᵤ healing report."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{_UASR_URL}/uasr/metrics")
        return resp.json()


@app.get("/uasr/drift/status")
async def uasr_drift_status(source_id: str = None):
    """Proxy: drift status."""
    params = {}
    if source_id:
        params["source_id"] = source_id
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{_UASR_URL}/uasr/drift/status", params=params)
        return resp.json()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_GATEWAY_PORT", "8000")),
    )
