"""
Queries Router
===============
SQL execution, query validation/linting, query history,
dashboard stats, and job control endpoints.
"""

import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from pathlib import Path

from shared.logging_config import get_logger
from shared.cache import dashboard_cache, query_cache
from shared.circuit_breaker import get_breaker
from shared.streaming_manager import streaming_manager, TOPIC_QUERY
from safety import SQLSafetyValidator
from insights import InsightsEngine
from connectors import ConnectorConfig, SourceType, PostgreSQLConnector, MySQLConnector, BigQueryConnector

logger = get_logger("aura.api_gateway.queries")

router = APIRouter(tags=["Queries"])


# ── In-memory stores ────────────────────────────────────────────────

_query_history_lock = threading.Lock()
_query_history_store: List[Dict[str, Any]] = []  # newest-first

_dashboard_counters_lock = threading.Lock()
_dashboard_counters: Dict[str, int] = {"total_rows": 0, "queries_run": 0}

_jobs_lock = threading.Lock()
_jobs_store: Dict[str, Dict[str, Any]] = {}

# Re-use the connections store from the connections router
from api_gateway.routers.connections import _connections_lock, _connections_store


def track_query(prompt: str, sql: str, q_status: str, rows: int, execution_time_ms: float):
    """Record a query execution — called by chat router too."""
    record = {
        "id": f"q_{int(datetime.now().timestamp() * 1000)}",
        "prompt": prompt,
        "sql": sql,
        "status": q_status,
        "rows": rows,
        "executionTime": round(execution_time_ms, 1),
        "timestamp": datetime.now().isoformat(),
    }
    with _query_history_lock:
        _query_history_store.insert(0, record)
        if len(_query_history_store) > 200:
            del _query_history_store[200:]
    with _dashboard_counters_lock:
        _dashboard_counters["queries_run"] += 1
        if q_status == "success":
            _dashboard_counters["total_rows"] += rows


# ── Models ───────────────────────────────────────────────────────────

class _ChatExecuteRequest(BaseModel):
    sql: str
    connection_id: Optional[str] = None


class ValidateQueryRequest(BaseModel):
    query: str
    dry_run_mode: bool = False
    max_rows: int = 10000


class ValidateQueryResponse(BaseModel):
    is_valid: bool
    risk_level: str
    warnings: List[str]
    errors: List[str]
    suggested_query: Optional[str]
    row_count_estimate: int
    estimated_execution_ms: Optional[float]


class ExecuteQueryRequest(BaseModel):
    query: str
    connector_type: str
    connector_config: Dict[str, Any]
    dry_run: bool = False


class ExecuteQueryResponse(BaseModel):
    success: bool
    data: Optional[List[Dict[str, Any]]]
    rows: int
    columns: List[str]
    insights: Optional[Dict[str, Any]]
    error: Optional[str]
    execution_time_ms: float


class QueryRequest(BaseModel):
    session_id: str
    prompt: str
    context: Optional[str] = None


# ── Execute endpoints ────────────────────────────────────────────────

@router.post("/execute")
async def execute_for_chat(req: _ChatExecuteRequest):
    """Execute SQL from the chat interface.

    • If *connection_id* is provided, proxy to the execution sandbox.
    • Otherwise, run against uploaded files via DuckDB.
    """
    start = time.time()
    sql = req.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL query is required")

    # Safety gate
    validator = SQLSafetyValidator()
    validation = validator.validate(sql)
    if not validation.is_valid:
        raise HTTPException(status_code=400, detail=f"Query blocked: {validation.errors}")

    # If there's a connection_id, proxy to the sandbox
    if req.connection_id:
        sandbox_url = os.getenv("EXECUTION_SANDBOX_URL", "http://localhost:8003")
        job_id = f"chat-{int(time.time()*1000)}"
        payload = {
            "job_id": job_id,
            "sql": sql,
            "connection_id": req.connection_id,
            "approved": True,
            "limit": int(os.getenv("DEFAULT_QUERY_LIMIT", "1000")),
        }
        breaker = get_breaker("execution_sandbox")
        await streaming_manager.publish_progress(TOPIC_QUERY, job_id, "Sending to execution sandbox", 0.2)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await breaker.call(
                    client.post(f"{sandbox_url}/execute_sql", json=payload),
                    fallback=None,
                )
            if resp is None:
                await streaming_manager.publish_error(TOPIC_QUERY, job_id, "Execution sandbox unavailable")
                return {"success": False, "error": "Execution sandbox unavailable (circuit open)", "data": [], "columns": []}
            resp.raise_for_status()
            data = resp.json()
            columns = data.get("columns", [])
            rows = data.get("rows", [])
            records = [dict(zip(columns, row)) for row in rows]
            elapsed = (time.time() - start) * 1000
            await streaming_manager.publish_complete(TOPIC_QUERY, job_id, {"rows": len(records), "execution_time_ms": round(elapsed, 1)})
            return {
                "success": True, "data": records, "columns": columns,
                "row_count": len(records), "execution_time_ms": round(elapsed, 1),
                "chart_spec": data.get("chart_spec"),
            }
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            try:
                detail = exc.response.json().get("detail", detail)
            except Exception:
                pass
            await streaming_manager.publish_error(TOPIC_QUERY, job_id, str(detail))
            return {"success": False, "error": str(detail), "data": [], "columns": []}
        except Exception as exc:
            await streaming_manager.publish_error(TOPIC_QUERY, job_id, str(exc))
            return {"success": False, "error": str(exc), "data": [], "columns": []}

    # No connection: execute against uploaded files via DuckDB
    try:
        import duckdb
        import pathlib
        from shared.data_utils import build_schema_context

        base = pathlib.Path(__file__).resolve().parent.parent.parent
        upload_dirs = [
            base / "data" / "uploads",
            base / "api_gateway" / "uploads",
            base.parent / "uploads",
        ]

        con = duckdb.connect(":memory:")
        build_schema_context(con, upload_dirs, use_llm=True)

        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        records = [dict(zip(columns, row)) for row in rows]

        conclusion = None
        if records:
            from agents.specialists.analysis_agent import AnalysisAgent
            from agents.base import AgentContext
            agent = AnalysisAgent()
            ctx = AgentContext(
                user_prompt="Explain these executed SQL results conceptually.",
                task_description="Analyze the executed query results.",
                upstream_results={"t2": {"records": records}},
            )
            analysis_res = await agent.execute(ctx)
            if analysis_res.succeeded:
                conclusion = analysis_res.output.get("conclusion")

        elapsed = (time.time() - start) * 1000
        con.close()
        return {
            "success": True, "data": records, "columns": columns,
            "row_count": len(records), "execution_time_ms": round(elapsed, 1),
            "conclusion": conclusion,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": [], "columns": []}


@router.post("/execute/query", response_model=ExecuteQueryResponse)
async def execute_query_with_insights(request: ExecuteQueryRequest):
    """Execute query with automatic insights generation."""
    try:
        validator = SQLSafetyValidator()
        validation = validator.validate(request.query)
        if not validation.is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Query validation failed: {validation.errors}")

        connector_config = ConnectorConfig(source_type=SourceType(request.connector_type), name=f"exec-{request.connector_type}", **request.connector_config)
        if request.connector_type == "postgresql":
            connector = PostgreSQLConnector(connector_config)
        elif request.connector_type == "mysql":
            connector = MySQLConnector(connector_config)
        elif request.connector_type == "bigquery":
            connector = BigQueryConnector(connector_config)
        else:
            raise ValueError(f"Unknown connector type: {request.connector_type}")

        start_time = time.time()
        await connector.connect()
        results = await connector.execute_query(request.query)
        await connector.disconnect()
        execution_time = (time.time() - start_time) * 1000

        conclusion = None
        if results:
            from agents.specialists.analysis_agent import AnalysisAgent
            from agents.base import AgentContext
            agent = AnalysisAgent()
            ctx = AgentContext(
                user_prompt="Explain these results conceptually.",
                task_description="Analyze the executed query results.",
                upstream_results={"t2": {"records": results}},
            )
            analysis_res = await agent.execute(ctx)
            if analysis_res.succeeded:
                conclusion = analysis_res.output.get("conclusion")

        columns = list(results[0].keys()) if results else []
        return ExecuteQueryResponse(
            success=True, data=results, rows=len(results), columns=columns,
            insights={"conclusion": conclusion} if conclusion else None,
            execution_time_ms=execution_time,
        )
    except Exception as e:
        return ExecuteQueryResponse(success=False, data=None, rows=0, columns=[], error=str(e), execution_time_ms=0)


@router.post("/generate_query")
async def generate_query_proxy(request: QueryRequest) -> Dict[str, Any]:
    """Proxy query generation to orchestration service."""
    target_url = os.getenv("ORCHESTRATION_SERVICE_URL", "http://localhost:8006/v1/orchestrations/query")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(target_url, json=request.model_dump())
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as http_exc:
        return {"status": "Error", "error_message": f"Orchestration error: {http_exc.response.text}", "final_query": "-- Error generating query"}
    except Exception as exc:
        return {"status": "Error", "error_message": f"Backend error: {str(exc)}", "final_query": "-- Error generating query"}


# ── Validation & Linting ─────────────────────────────────────────────

@router.post("/validate/query", response_model=ValidateQueryResponse)
async def validate_query(request: ValidateQueryRequest):
    """Validate SQL query for safety and performance."""
    validator = SQLSafetyValidator(max_rows=request.max_rows, dry_run_only=request.dry_run_mode)
    result = validator.validate(request.query)
    from safety import QueryPlanner
    exec_time, _ = QueryPlanner.estimate_execution_time(request.query)
    return ValidateQueryResponse(
        is_valid=result.is_valid, risk_level=result.risk_level.value,
        warnings=result.warnings, errors=result.errors,
        suggested_query=result.suggested_query,
        row_count_estimate=result.row_count_estimate,
        estimated_execution_ms=exec_time,
    )


@router.post("/lint/query")
async def lint_query(query: str):
    """Lint SQL query for style and optimization."""
    validator = SQLSafetyValidator()
    suggestions = validator.lint_query(query)
    return {
        "suggestions": suggestions,
        "suggested_query": validator.add_safety_limit(query) if "LIMIT" not in query.upper() else None,
    }


# ── Insights ─────────────────────────────────────────────────────────

@router.post("/analyze/results")
async def analyze_results(query: str, results: List[Dict[str, Any]], column_profiles: Optional[Dict[str, Any]] = None):
    """Generate insights from query results."""
    engine = InsightsEngine()
    analysis = engine.analyze(query, results, column_profiles)
    return analysis


# ── Query History ────────────────────────────────────────────────────

@router.get("/query-history")
async def get_query_history(limit: int = 50, status_filter: Optional[str] = None):
    """Get server-side query history."""
    with _query_history_lock:
        records = list(_query_history_store)
    if status_filter and status_filter != "all":
        records = [r for r in records if r.get("status") == status_filter]
    return {"success": True, "queries": records[:limit], "total": len(records)}


@router.post("/query-history")
async def save_query_history(payload: Dict[str, Any]):
    """Save a query execution record."""
    record = {
        "id": payload.get("id", f"q_{int(datetime.now().timestamp() * 1000)}"),
        "prompt": payload.get("prompt", ""), "sql": payload.get("sql", ""),
        "status": payload.get("status", "success"), "rows": payload.get("rows", 0),
        "executionTime": payload.get("executionTime", 0),
        "timestamp": payload.get("timestamp", datetime.now().isoformat()),
    }
    with _query_history_lock:
        _query_history_store.insert(0, record)
        if len(_query_history_store) > 200:
            del _query_history_store[200:]
    return {"success": True, "id": record["id"]}


# ── Dashboard Stats ──────────────────────────────────────────────────

@router.get("/dashboard/stats")
async def get_dashboard_stats():
    """Real-time dashboard statistics (cached 30 s)."""
    cached = await dashboard_cache.get("dashboard:stats")
    if cached is not None:
        return cached

    file_count = 0
    total_file_rows = 0
    try:
        base = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        upload_dir = base / "data" / "uploads"
        if upload_dir.exists():
            import duckdb
            for f in upload_dir.iterdir():
                if f.is_file() and f.suffix.lower() in (".csv", ".json", ".parquet", ".xlsx", ".xls"):
                    file_count += 1
                    try:
                        con = duckdb.connect(":memory:")
                        fp = str(f).replace(chr(92), '/')
                        if f.suffix.lower() == ".csv":
                            count = con.execute(f"SELECT COUNT(*) FROM read_csv_auto('{fp}')").fetchone()[0]
                        elif f.suffix.lower() == ".parquet":
                            count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fp}')").fetchone()[0]
                        elif f.suffix.lower() == ".json":
                            count = con.execute(f"SELECT COUNT(*) FROM read_json_auto('{fp}')").fetchone()[0]
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

    result = {
        "total_rows": total_file_rows + tracked_rows,
        "active_sources": file_count + active_conns,
        "total_connections": total_conns,
        "file_sources": file_count,
        "queries_run": queries_run,
        "system_health": "healthy",
        "uptime_percentage": 99.9,
    }
    await dashboard_cache.set("dashboard:stats", result)
    return result


# ── Job Control ──────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/approve")
async def approve_job(job_id: str):
    """Approve a pending job for execution."""
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        if job:
            job["status"] = "approved"
            job["approved_at"] = datetime.now().isoformat()
            return {"success": True, "job_id": job_id, "status": "approved"}
    return {"success": True, "job_id": job_id, "status": "approved", "message": "Job approved (auto)"}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a pending or running job."""
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        if job:
            job["status"] = "cancelled"
            job["cancelled_at"] = datetime.now().isoformat()
            return {"success": True, "job_id": job_id, "status": "cancelled"}
    return {"success": True, "job_id": job_id, "status": "cancelled", "message": "Job cancelled"}
