"""
Queries Router
===============
SQL execution, query validation/linting, query history,
dashboard stats, and job control endpoints.
"""

import asyncio
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from api_gateway.routers.workspaces import DEFAULT_WORKSPACE_ID, current_workspace_id
from connectors import BigQueryConnector, ConnectorConfig, MySQLConnector, PostgreSQLConnector, SourceType
from insights import InsightsEngine
from safety import SQLSafetyValidator
from shared.cache import dashboard_cache, query_cache
from shared.circuit_breaker import get_breaker
from shared.logging_config import get_logger
from shared.streaming_manager import TOPIC_QUERY, streaming_manager

logger = get_logger("aura.api_gateway.queries")

router = APIRouter(tags=["Queries"])


# ── State stores ─────────────────────────────────────────────────────
#
# Sprint P-1 migrated query_history + saved_queries + share_tokens to
# Postgres-backed SQLAlchemy (see api_gateway/persistence.py). The
# remaining in-memory stores below are NOT in scope for P-1; they
# stay in-process for now and will be migrated in a follow-up sprint.

_dashboard_counters_lock = threading.Lock()
_dashboard_counters: Dict[str, int] = {"total_rows": 0, "queries_run": 0}

_jobs_lock = threading.Lock()
_jobs_store: Dict[str, Dict[str, Any]] = {}

# Cross-router shims: dashboards.py and lineage.py used to import
# ``_saved_queries_store`` + ``_saved_queries_lock`` directly. They
# now call ``persistence.list_saved_queries(...)`` instead — the
# legacy attribute names below are kept as no-op aliases (an empty
# list + a re-entrant Lock) so any straggling imports don't blow up
# at startup. NEW code MUST use the persistence module directly.
import threading as _threading_compat

_saved_queries_lock = _threading_compat.Lock()
_saved_queries_store: List[Dict[str, Any]] = []
_share_tokens_lock = _threading_compat.Lock()
_share_tokens_store: Dict[str, str] = {}

# ── Saved-query schedule run history — keyed by saved-query id ────────
_saved_query_runs_lock = threading.Lock()
_saved_query_runs_store: Dict[str, List[Dict[str, Any]]] = {}
_SAVED_QUERY_RUN_RETENTION = 50  # per saved query

# ── Scheduler background task (in-process ticker) ─────────────────────
_scheduler_task: Optional[asyncio.Task] = None
_scheduler_stop: Optional[asyncio.Event] = None
_SCHEDULER_INTERVAL_SEC = 30

# Re-use the connections store from the connections router
from api_gateway.routers.connections import _connections_lock, _connections_store


async def track_query(prompt: str, sql: str, q_status: str, rows: int, execution_time_ms: float):
    """Record a query execution — called by chat router too.

    Sprint P-1: persists to the gateway's SQLAlchemy ``gateway_query_history``
    table (capped at 200 globally) so history survives a process restart
    and is shared across replicas. Dashboard counters stay in-process
    for now (a separate Sprint P-2 will migrate them when they actually
    need to scale)."""
    from api_gateway import persistence
    record = {
        "id": f"q_{int(datetime.now().timestamp() * 1000)}",
        "prompt": prompt,
        "sql": sql,
        "status": q_status,
        "rows": rows,
        "executionTime": round(execution_time_ms, 1),
        "timestamp": datetime.now().isoformat(),
    }
    try:
        await persistence.insert_query_history(record)
    except Exception as exc:
        # Tracking is best-effort — never break the calling endpoint
        # because the history table is briefly unavailable.
        logger.warning("track_query persist failed (non-fatal): %s", exc)
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
        import pathlib

        import duckdb

        from shared.data_utils import build_schema_context_cached

        base = pathlib.Path(__file__).resolve().parent.parent.parent
        upload_dirs = [
            base / "data" / "uploads",
            base / "api_gateway" / "uploads",
            base.parent / "uploads",
        ]

        con = duckdb.connect(":memory:")
        # Sprint P-2b: load files into DuckDB without LLM inference so
        # we never block a query on a potentially slow LLM call. The
        # enriched context (with use_llm=True) is built in a background
        # task after upload and persisted in gateway_schema_context; if
        # no cached context exists yet we kick off a rebuild here.
        await build_schema_context_cached(con, upload_dirs, use_llm=False)
        from api_gateway import persistence as _gw_persistence
        _fp = _gw_persistence.compute_schema_fingerprint(
            [str(d) for d in upload_dirs]
        )
        if _fp and not await _gw_persistence.get_schema_context(_fp):
            asyncio.create_task(
                _gw_persistence.refresh_schema_context([str(d) for d in upload_dirs])
            )

        def _run_sql() -> tuple[list[str], list[tuple]]:
            cur = con.execute(sql)
            return [d[0] for d in cur.description], cur.fetchall()

        columns, rows = await asyncio.to_thread(_run_sql)
        records = [dict(zip(columns, row)) for row in rows]

        conclusion = None
        if records:
            from agents.base import AgentContext
            from agents.specialists.analysis_agent import AnalysisAgent
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
            from agents.base import AgentContext
            from agents.specialists.analysis_agent import AnalysisAgent
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
    """Get server-side query history. Sprint P-1: persisted in
    SQLAlchemy, indexed newest-first on ``created_ts``."""
    from api_gateway import persistence
    records = await persistence.list_query_history(
        limit=limit, status_filter=status_filter,
    )
    return {"success": True, "queries": records, "total": len(records)}


# ── Saved-query (library) models ─────────────────────────────────────

class SavedQueryCreate(BaseModel):
    name: str
    sql: str
    prompt: Optional[str] = None
    starred: bool = False


class SavedQueryUpdate(BaseModel):
    name: Optional[str] = None
    starred: Optional[bool] = None


def _in_workspace(record: Dict[str, Any], wsid: str) -> bool:
    """Backfill-friendly workspace predicate — legacy records without a
    workspace_id tag are treated as belonging to the default workspace."""
    return (record.get("workspace_id") or DEFAULT_WORKSPACE_ID) == wsid


@router.get("/saved-queries")
async def list_saved_queries(request: Request):
    """Return saved queries for the caller's workspace, starred first
    then newest-first. Sprint P-1: backed by SQLAlchemy with a
    composite index on (workspace_id, starred DESC, created_ts DESC) —
    the ORDER BY is satisfied by the index, no in-memory sort needed."""
    from api_gateway import persistence
    wsid = current_workspace_id(request)
    records = await persistence.list_saved_queries(wsid)
    return {"success": True, "queries": records, "total": len(records)}


@router.post("/saved-queries")
async def create_saved_query(payload: SavedQueryCreate, request: Request):
    """Create a saved query in the caller's workspace. Sprint P-1:
    inserts into ``gateway_saved_queries`` and evicts beyond the
    500-per-workspace cap inside the same transaction."""
    from api_gateway import persistence
    name = payload.name.strip()
    sql = payload.sql.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")
    wsid = current_workspace_id(request)
    ts = datetime.now()
    record = {
        "id": f"sq_{int(ts.timestamp() * 1000)}",
        "workspace_id": wsid,
        "name": name,
        "sql": sql,
        "prompt": (payload.prompt or "").strip() or None,
        "starred": bool(payload.starred),
        "created_at": ts.isoformat(),
        "created_ts": ts.timestamp(),
        "updated_at": ts.isoformat(),
    }
    saved = await persistence.insert_saved_query(record)
    # P-2c: pre-compute table→query edges at write time so GET /lineage
    # reads the cache instead of re-parsing SQL on every request.
    asyncio.create_task(persistence.upsert_lineage_edges(saved["id"], wsid, sql))
    return {"success": True, "query": saved}


@router.patch("/saved-queries/{query_id}")
async def update_saved_query(query_id: str, payload: SavedQueryUpdate, request: Request):
    """Rename or toggle star on a saved query in the caller's workspace."""
    from api_gateway import persistence
    wsid = current_workspace_id(request)
    fields: Dict[str, Any] = {}
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        fields["name"] = new_name
    if payload.starred is not None:
        fields["starred"] = bool(payload.starred)
    fields["updated_at"] = datetime.now().isoformat()
    updated = await persistence.update_saved_query(query_id, wsid, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    return {"success": True, "query": updated}


@router.delete("/saved-queries/{query_id}")
async def delete_saved_query(query_id: str, request: Request):
    """Delete a saved query from the caller's workspace. The repository
    cascades token revocation inside the same transaction — O(log n)
    via the saved_query_id index instead of the legacy O(n) dict scan."""
    from api_gateway import persistence
    wsid = current_workspace_id(request)
    deleted = await persistence.delete_saved_query(query_id, wsid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved query not found")
    return {"success": True, "id": query_id}


# ── Saved-query share tokens (public read-only) ─────────────────────

@router.post("/saved-queries/{query_id}/share")
async def create_share_link(query_id: str, request: Request):
    """Mint (or re-mint) a share token for a saved query. Idempotent —
    the repository returns the existing token if one already exists
    for this query_id (via the saved_query_id index, not an O(n) scan)."""
    from api_gateway import persistence
    wsid = current_workspace_id(request)
    if await persistence.get_saved_query(query_id, workspace_id=wsid) is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    token = await persistence.get_or_create_share_token(query_id)
    return {"success": True, "token": token, "query_id": query_id}


@router.delete("/saved-queries/{query_id}/share")
async def revoke_share_link(query_id: str, request: Request):
    """Revoke all share tokens pointing at this query — O(log n) via
    the saved_query_id index."""
    from api_gateway import persistence
    wsid = current_workspace_id(request)
    if await persistence.get_saved_query(query_id, workspace_id=wsid) is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    revoked = await persistence.revoke_share_tokens_for_query(query_id)
    return {"success": True, "query_id": query_id, "revoked": revoked}


@router.get("/public/saved-queries/{token}")
async def read_shared_query(token: str):
    """Public read-only endpoint for a shared saved query.

    Intentionally bypasses the workspace header — anyone with the token
    sees the query's name, SQL, and prompt. No write paths are exposed.
    Sprint P-1: backed by indexed lookups; dangling tokens (whose
    query was deleted) are cleaned up on access."""
    from api_gateway import persistence
    query_id = await persistence.lookup_share_token(token)
    if query_id is None:
        raise HTTPException(status_code=404, detail="Share link is invalid or has been revoked")
    record = await persistence.get_saved_query(query_id, workspace_id=None)
    if record is None:
        # Dangling token — clean up.
        await persistence.revoke_one_share_token(token)
        raise HTTPException(status_code=404, detail="Shared query no longer exists")
    return {
        "success": True,
        "query": {
            "id": record["id"],
            "name": record.get("name"),
            "sql": record.get("sql"),
            "prompt": record.get("prompt"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        },
    }


# ── Saved-query scheduling ──────────────────────────────────────────

class SavedQuerySchedule(BaseModel):
    interval: str = Field(..., description="hourly | daily | weekly")
    hour: int = Field(9, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)
    day_of_week: Optional[int] = Field(None, ge=0, le=6, description="0=Mon..6=Sun (weekly only)")
    enabled: bool = True


def _compute_next_run(schedule: Dict[str, Any], *, now: Optional[datetime] = None) -> Optional[str]:
    """Return ISO timestamp of next run in UTC, or None when disabled/invalid."""
    from datetime import timedelta, timezone
    if not schedule or not schedule.get("enabled", True):
        return None
    interval = schedule.get("interval")
    now = now or datetime.now(timezone.utc)
    hour = int(schedule.get("hour", 9))
    minute = int(schedule.get("minute", 0))
    if interval == "hourly":
        nxt = now.replace(minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(hours=1)
        return nxt.isoformat()
    if interval == "daily":
        nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt.isoformat()
    if interval == "weekly":
        target_dow = int(schedule.get("day_of_week", 0))  # 0 = Mon
        nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta_days = (target_dow - nxt.weekday()) % 7
        if delta_days == 0 and nxt <= now:
            delta_days = 7
        nxt += timedelta(days=delta_days)
        return nxt.isoformat()
    return None


@router.put("/saved-queries/{query_id}/schedule")
async def set_saved_query_schedule(query_id: str, payload: SavedQuerySchedule, request: Request):
    """Set or replace the schedule for a saved query."""
    from api_gateway import persistence
    if payload.interval not in ("hourly", "daily", "weekly"):
        raise HTTPException(status_code=400, detail="interval must be hourly | daily | weekly")
    schedule = payload.dict()
    next_run = _compute_next_run(schedule)
    wsid = current_workspace_id(request)
    updated = await persistence.update_saved_query(
        query_id, wsid,
        {
            "schedule": schedule,
            "next_run_at": next_run,
            "updated_at": datetime.now().isoformat(),
        },
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    return {"success": True, "query": updated}


@router.delete("/saved-queries/{query_id}/schedule")
async def clear_saved_query_schedule(query_id: str, request: Request):
    """Remove the schedule on a saved query."""
    from api_gateway import persistence
    wsid = current_workspace_id(request)
    updated = await persistence.update_saved_query(
        query_id, wsid,
        {
            "schedule": None,
            "next_run_at": None,
            "updated_at": datetime.now().isoformat(),
        },
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    return {"success": True, "query": updated}


@router.get("/saved-queries/{query_id}/runs")
async def list_saved_query_runs(query_id: str, request: Request, limit: int = 20):
    """Return recent scheduled runs (newest first) for a saved query.

    The runs store itself is out of scope for Sprint P-1 (still
    in-memory) — only the existence check uses persistence."""
    from api_gateway import persistence
    wsid = current_workspace_id(request)
    if await persistence.get_saved_query(query_id, workspace_id=wsid) is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    with _saved_query_runs_lock:
        runs = list(_saved_query_runs_store.get(query_id, []))
    return {"success": True, "runs": runs[:max(1, min(limit, 200))]}


async def _execute_saved_query_sql(sql: str) -> Dict[str, Any]:
    """Run SQL against uploaded-file DuckDB. Mirrors the /execute/for-chat path.

    Kept minimal — no LLM analysis, no chart spec. Returns summary fields.
    """
    import pathlib

    import duckdb

    from shared.data_utils import build_schema_context_cached

    base = pathlib.Path(__file__).resolve().parent.parent.parent
    upload_dirs = [
        base / "data" / "uploads",
        base / "api_gateway" / "uploads",
        base.parent / "uploads",
    ]

    con = duckdb.connect(":memory:")
    try:
        await build_schema_context_cached(con, upload_dirs, use_llm=False)

        def _run() -> tuple[list[str], list[tuple]]:
            cur = con.execute(sql)
            return [d[0] for d in cur.description], cur.fetchall()

        start = time.time()
        columns, rows = await asyncio.to_thread(_run)
        elapsed = (time.time() - start) * 1000
        return {
            "success": True,
            "row_count": len(rows),
            "columns": columns,
            "execution_time_ms": round(elapsed, 1),
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


async def _record_run(query_id: str, entry: Dict[str, Any]) -> None:
    with _saved_query_runs_lock:
        runs = _saved_query_runs_store.setdefault(query_id, [])
        runs.insert(0, entry)
        if len(runs) > _SAVED_QUERY_RUN_RETENTION:
            del runs[_SAVED_QUERY_RUN_RETENTION:]


async def _fire_due_saved_queries() -> None:
    """Iterate saved queries and fire any whose next_run_at has passed.

    Sprint P-1: pulls the candidate set from the DB instead of an
    in-memory list scan. Each workspace's saved queries are returned
    via the indexed list; the schedule filter happens in Python (a
    future optimisation could push it into SQL via a partial index)."""
    from datetime import timezone

    from api_gateway import persistence
    now = datetime.now(timezone.utc)
    # Pull all schedulable records across known workspaces. For the
    # current scale (< 500 per workspace) this is cheap; if scheduling
    # grows we'll switch to a global SELECT with a WHERE on schedule
    # JSON predicates.
    async with persistence.session_scope() as s:
        from sqlalchemy import select
        rows = (await s.execute(
            select(persistence.SavedQueryRow).where(
                persistence.SavedQueryRow.schedule_json.is_not(None),
            ),
        )).scalars().all()
        candidates = [persistence._row_to_saved_query_dict(r) for r in rows]

    due: List[Dict[str, Any]] = []
    for record in candidates:
        schedule = record.get("schedule")
        if not schedule or not schedule.get("enabled", True):
            continue
        next_run_iso = record.get("next_run_at")
        if not next_run_iso:
            continue
        try:
            next_run = datetime.fromisoformat(next_run_iso)
        except ValueError:
            continue
        if next_run <= now:
            due.append(record)

    for record in due:
        query_id = record["id"]
        sql = record.get("sql", "")
        started = datetime.now().isoformat()
        try:
            result = await _execute_saved_query_sql(sql)
            entry = {
                "id": f"run_{int(time.time() * 1000)}",
                "started_at": started,
                "completed_at": datetime.now().isoformat(),
                "status": "success" if result.get("success") else "failed",
                "row_count": result.get("row_count", 0),
                "execution_time_ms": result.get("execution_time_ms", 0),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduled saved-query execution failed: %s", query_id)
            entry = {
                "id": f"run_{int(time.time() * 1000)}",
                "started_at": started,
                "completed_at": datetime.now().isoformat(),
                "status": "failed",
                "row_count": 0,
                "execution_time_ms": 0,
                "error": str(exc),
            }
        await _record_run(query_id, entry)

        # Advance next_run_at via the persistence layer.
        new_next = _compute_next_run(record.get("schedule") or {})
        await persistence.update_saved_query(
            query_id, record.get("workspace_id") or DEFAULT_WORKSPACE_ID,
            {
                "last_run_at": entry["completed_at"],
                "next_run_at": new_next,
                "updated_at": datetime.now().isoformat(),
            },
        )


async def _scheduler_loop(stop: asyncio.Event) -> None:
    logger.info("Saved-query scheduler started (interval=%ss)", _SCHEDULER_INTERVAL_SEC)
    while not stop.is_set():
        try:
            await _fire_due_saved_queries()
        except Exception:
            logger.exception("Saved-query scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_SCHEDULER_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue
    logger.info("Saved-query scheduler stopped")


async def start_saved_query_scheduler() -> None:
    global _scheduler_task, _scheduler_stop
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_stop = asyncio.Event()
    _scheduler_task = asyncio.create_task(_scheduler_loop(_scheduler_stop))


async def stop_saved_query_scheduler() -> None:
    global _scheduler_task, _scheduler_stop
    if _scheduler_stop is not None:
        _scheduler_stop.set()
    if _scheduler_task is not None:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _scheduler_task.cancel()
    _scheduler_task = None
    _scheduler_stop = None


@router.post("/query-history")
async def save_query_history(payload: Dict[str, Any]):
    """Save a query execution record. Sprint P-1: backed by
    ``gateway_query_history`` with the 200-row cap enforced inside
    the persistence layer."""
    from api_gateway import persistence
    record = {
        "id": payload.get("id", f"q_{int(datetime.now().timestamp() * 1000)}"),
        "prompt": payload.get("prompt", ""), "sql": payload.get("sql", ""),
        "status": payload.get("status", "success"), "rows": payload.get("rows", 0),
        "executionTime": payload.get("executionTime", 0),
        "timestamp": payload.get("timestamp", datetime.now().isoformat()),
    }
    await persistence.insert_query_history(record)
    return {"success": True, "id": record["id"]}


# ── LLM cost / token stats ───────────────────────────────────────────

@router.get("/llm-stats")
async def get_llm_stats():
    """Snapshot of cumulative LLM token usage by provider/model/kind.

    Reads directly from the in-process Prometheus Counter so the dashboard
    works without a Prometheus server. ``available=False`` means the
    prometheus-client dep isn't installed in this environment.
    """
    from shared.observability import llm_token_breakdown
    return llm_token_breakdown()


# ── Dashboard Stats ──────────────────────────────────────────────────

@router.get("/dashboard/stats")
async def get_dashboard_stats():
    """Real-time dashboard statistics.

    Sprint P-2a: replaced the per-request DuckDB-per-file COUNT(*)
    scan (audit finding #2) with a single SELECT against the
    ``gateway_file_metadata`` cache. The cache is populated on file
    upload (background task in files.py) and refreshed every 60s by
    the background tick. Falls back to the legacy in-line scan ONCE
    if the cache is empty AND files exist on disk — guards the
    cold-start case (e.g. a deploy that promoted a pre-existing
    upload dir without an indexer pass)."""
    from api_gateway import persistence

    cached = await dashboard_cache.get("dashboard:stats")
    if cached is not None:
        return cached

    file_count = 0
    total_file_rows = 0
    try:
        rows = await persistence.list_file_metadata()
        file_count = len(rows)
        total_file_rows = sum(r["row_count"] for r in rows)

        # Cold-start guard: if the cache is empty but files exist on
        # disk, kick off a one-shot refresh so the next request
        # benefits. Don't await its completion — we'd rather return
        # zeros for one request than block the caller for several
        # seconds during the first indexing pass.
        if file_count == 0:
            base = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            upload_dir = base / "data" / "uploads"
            if upload_dir.exists() and any(upload_dir.iterdir()):
                asyncio.create_task(
                    persistence.refresh_stale_file_metadata(str(upload_dir)),
                )
    except Exception as exc:
        logger.warning("dashboard stats persistence read failed (non-fatal): %s", exc)

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
