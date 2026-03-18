"""
Tool Implementations
====================
Concrete tool functions that agents call.  Each wraps an existing AURA
microservice endpoint (API Gateway 8000, Code-Gen 8001, Connectors 8002,
Execution Sandbox 8003, Scheduler 8004, Insights 8005).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from agents.tool_registry import ToolRegistry, Tool

# ── Service base URLs ─────────────────────────────────────────────────
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
    """Execute a SQL query via the Execution Sandbox."""
    return await _post(_SANDBOX, "/execute", {"query": query, "connection_id": connection_id})


async def ingest_and_profile(*, file_path: str) -> Dict[str, Any]:
    """Ingest a file and return its profile via the Connectors service."""
    return await _post(_CONNECTORS, "/ingest", {"file_path": file_path})


async def introspect_database(*, connection_string: str = "", connection_id: str = "default") -> Dict[str, Any]:
    """Return schema metadata for a database."""
    return await _post(_CONNECTORS, "/introspect", {
        "connection_string": connection_string,
        "connection_id": connection_id,
    })


async def list_uploaded_files() -> List[str]:
    """List all uploaded files known to the gateway."""
    return await _get(_GATEWAY, "/files")


async def recommend_indexes(*, table: str, query_patterns: List[str] | None = None) -> Dict[str, Any]:
    """Ask the Insights service for index recommendations."""
    return await _post(_INSIGHTS, "/recommend/indexes", {
        "table": table,
        "query_patterns": query_patterns or [],
    })


async def code_gen_service(*, prompt: str, schema_context: str = "") -> Any:
    """Generate SQL via the Code-Gen microservice."""
    return await _post(_CODEGEN, "/generate", {
        "prompt": prompt,
        "schema_context": schema_context,
    })


async def create_schedule(
    *,
    pipeline_id: str,
    cron: str,
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Register a scheduled pipeline with the Scheduler service."""
    return await _post(_SCHEDULER, "/schedules", {
        "pipeline_id": pipeline_id,
        "cron": cron,
        "payload": payload or {},
    })


async def get_insights(*, query: str) -> Dict[str, Any]:
    """Fetch analytical insights for a query."""
    return await _post(_INSIGHTS, "/insights", {"query": query})


# ── Registration ──────────────────────────────────────────────────────

def register_all_tools(registry: ToolRegistry) -> ToolRegistry:
    """Register every tool function with the given registry."""
    registry.register(Tool(
        name="execute_sql",
        fn=execute_sql,
        description="Execute a SQL query and return results.",
        category="execution",
    ))
    registry.register(Tool(
        name="ingest_and_profile",
        fn=ingest_and_profile,
        description="Ingest a file and profile its columns.",
        category="ingestion",
    ))
    registry.register(Tool(
        name="introspect_database",
        fn=introspect_database,
        description="Inspect database schema and return table/column metadata.",
        category="connectors",
    ))
    registry.register(Tool(
        name="list_uploaded_files",
        fn=list_uploaded_files,
        description="List all files uploaded to AURA.",
        category="ingestion",
    ))
    registry.register(Tool(
        name="recommend_indexes",
        fn=recommend_indexes,
        description="Get index recommendations for a table.",
        category="optimization",
    ))
    registry.register(Tool(
        name="code_gen_service",
        fn=code_gen_service,
        description="Generate SQL from natural language via the code-gen service.",
        category="generation",
    ))
    registry.register(Tool(
        name="create_schedule",
        fn=create_schedule,
        description="Schedule a pipeline with a cron expression.",
        category="scheduling",
    ))
    registry.register(Tool(
        name="get_insights",
        fn=get_insights,
        description="Get analytical insights for a query.",
        category="insights",
    ))
    return registry
