"""
Tool Implementations
====================
Concrete tool functions that agents call.  Each wraps an existing AURA
microservice endpoint (API Gateway 8000, Code-Gen 8001, Connectors 8002,
Execution Sandbox 8003, Scheduler 8004, Insights 8005).

Route mapping (verified against actual service main.py files):
  - Sandbox   POST /execute_sql    (ExecutionJob body)
  - CodeGen   POST /generate_code  (PlanStep body)
  - Scheduler POST /jobs           (CreateJobRequest body)
  - Insights  POST /analyze        (AnalyzeRequest body)
  - Insights  POST /recommend-indexes (custom endpoint)
  - Connectors POST /ingest        (custom endpoint)
  - Connectors POST /introspect    (custom endpoint)
  - Gateway   GET  /files
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import httpx

from agents.tool_registry import Tool, ToolRegistry

# ── Service base URLs (match orchestrator.py) ─────────────────────────
_GATEWAY    = os.getenv("AURA_GATEWAY_URL",    "http://localhost:8000")
_CODEGEN    = os.getenv("AURA_CODEGEN_URL",    "http://localhost:8001")
_CONNECTORS = os.getenv("AURA_CONNECTORS_URL", "http://localhost:8002")
_SANDBOX    = os.getenv("AURA_SANDBOX_URL",    "http://localhost:8003")
_SCHEDULER  = os.getenv("AURA_SCHEDULER_URL",  "http://localhost:8004")
_INSIGHTS   = os.getenv("AURA_INSIGHTS_URL",   "http://localhost:8005")

_TIMEOUT = float(os.getenv("AURA_TOOL_TIMEOUT", "60"))


async def _post(base: str, path: str, payload: Dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{base}{path}", json=payload or {})
        resp.raise_for_status()
        return resp.json()


async def _get(base: str, path: str, params: Dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{base}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()


# ── Tool functions ────────────────────────────────────────────────────

async def execute_sql(*, query: str, connection_id: str = "default") -> Any:
    """Execute a SQL query via the Execution Sandbox.

    Sandbox route: POST /execute_sql
    Body schema: ExecutionJob(job_id, sql, connection_id, limit, approved)
    """
    return await _post(_SANDBOX, "/execute_sql", {
        "job_id": str(uuid.uuid4()),
        "sql": query,
        "connection_id": connection_id,
        "limit": int(os.getenv("DEFAULT_QUERY_LIMIT", "1000")),
        "approved": True,
    })


async def ingest_and_profile(*, file_path: str) -> Dict[str, Any]:
    """Ingest a file and return its profile via the Connectors service.

    Connectors route: POST /ingest  (custom endpoint added for agent tools)
    """
    return await _post(_CONNECTORS, "/ingest", {"file_path": file_path})


async def introspect_database(
    *,
    connector_type: str = "postgresql",
    config: Dict[str, Any] | None = None,
    connection_id: str = "default",
) -> Dict[str, Any]:
    """Return schema metadata (tables + columns) for a database.

    Connectors route: POST /introspect  (custom endpoint added for agent tools)
    Falls back to POST /tables if introspect is unavailable.
    """
    payload: Dict[str, Any] = {
        "connector_type": connector_type,
        "config": config or {},
    }
    try:
        return await _post(_CONNECTORS, "/introspect", payload)
    except httpx.HTTPStatusError:
        # Fallback: use /tables to list table names
        result = await _post(_CONNECTORS, "/tables", payload)
        return {"tables": result.get("tables", []), "source": "tables_fallback"}


async def list_uploaded_files() -> Any:
    """List all uploaded files known to the gateway.

    Gateway route: GET /files
    """
    return await _get(_GATEWAY, "/files")


async def recommend_indexes(
    *,
    table: str,
    query_patterns: List[str] | None = None,
) -> Dict[str, Any]:
    """Ask the Insights service for index recommendations.

    Insights route: POST /recommend-indexes  (custom endpoint added for agent tools)
    """
    return await _post(_INSIGHTS, "/recommend-indexes", {
        "table": table,
        "query_patterns": query_patterns or [],
    })


async def code_gen_service(*, prompt: str, schema_context: str = "") -> Any:
    """Generate SQL via the Code-Gen microservice.

    CodeGen route: POST /generate_code
    Body schema: PlanStep(step, task, chart_type)
    """
    return await _post(_CODEGEN, "/generate_code", {
        "step": prompt,
        "task": schema_context or None,
        "chart_type": None,
    })


async def create_schedule(
    *,
    name: str,
    connection_id: str = "default",
    query: str,
    schedule_type: str = "daily",
    cron_expression: str | None = None,
    schedule_config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Register a scheduled job with the Scheduler service.

    Scheduler route: POST /jobs
    Body schema: CreateJobRequest(name, connection_id, query, schedule_type, ...)
    """
    return await _post(_SCHEDULER, "/jobs", {
        "name": name,
        "connection_id": connection_id,
        "query": query,
        "schedule_type": schedule_type,
        "cron_expression": cron_expression,
        "schedule_config": schedule_config or {},
    })


async def get_insights(*, query: str, results: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Fetch analytical insights for query results.

    Insights route: POST /analyze
    Body schema: AnalyzeRequest(query, results, column_profiles)
    """
    return await _post(_INSIGHTS, "/analyze", {
        "query": query,
        "results": results or [],
    })


# ── Registration ──────────────────────────────────────────────────────

def register_all_tools(registry: ToolRegistry) -> ToolRegistry:
    """Register every tool function with the given registry."""
    registry.register(Tool(
        name="execute_sql",
        fn=execute_sql,
        description=(
            "Execute a SQL query via the Execution Sandbox and return "
            "columns, rows, and chart_spec. Params: query (str), "
            "connection_id (str, default 'default')."
        ),
        category="execution",
    ))
    registry.register(Tool(
        name="ingest_and_profile",
        fn=ingest_and_profile,
        description=(
            "Ingest a file and return its column profile. "
            "Params: file_path (str)."
        ),
        category="ingestion",
    ))
    registry.register(Tool(
        name="introspect_database",
        fn=introspect_database,
        description=(
            "Inspect database schema and return table/column metadata. "
            "Params: connector_type (str, default 'postgresql'), "
            "config (dict), connection_id (str)."
        ),
        category="connectors",
    ))
    registry.register(Tool(
        name="list_uploaded_files",
        fn=list_uploaded_files,
        description="List all files uploaded to AURA. No params.",
        category="ingestion",
    ))
    registry.register(Tool(
        name="recommend_indexes",
        fn=recommend_indexes,
        description=(
            "Get index recommendations for a table based on query patterns. "
            "Params: table (str), query_patterns (list[str])."
        ),
        category="optimization",
    ))
    registry.register(Tool(
        name="code_gen_service",
        fn=code_gen_service,
        description=(
            "Generate SQL from natural language via the code-gen service. "
            "Params: prompt (str), schema_context (str)."
        ),
        category="generation",
    ))
    registry.register(Tool(
        name="create_schedule",
        fn=create_schedule,
        description=(
            "Create a scheduled job. Params: name (str), query (str), "
            "connection_id (str), schedule_type (str), "
            "cron_expression (str), schedule_config (dict)."
        ),
        category="scheduling",
    ))
    registry.register(Tool(
        name="get_insights",
        fn=get_insights,
        description=(
            "Get analytical insights for query results. "
            "Params: query (str), results (list[dict])."
        ),
        category="insights",
    ))
    return registry
