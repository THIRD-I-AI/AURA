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


# ── In-memory stores ────────────────────────────────────────────────

_query_history_lock = threading.Lock()
_query_history_store: List[Dict[str, Any]] = []  # newest-first

_dashboard_counters_lock = threading.Lock()
_dashboard_counters: Dict[str, int] = {"total_rows": 0, "queries_run": 0}

_jobs_lock = threading.Lock()
_jobs_store: Dict[str, Dict[str, Any]] = {}

# ── Saved queries (library) — in-memory, newest-first ─────────────────
_saved_queries_lock = threading.Lock()
_saved_queries_store: List[Dict[str, Any]] = []

# ── Share tokens for saved queries — maps token -> saved_query_id ─────
_share_tokens_lock = threading.Lock()
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
        await build_schema_context_cached(con, upload_dirs, use_llm=True)

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
    """Get server-side query history."""
    with _query_history_lock:
        records = list(_query_history_store)
    if status_filter and status_filter != "all":
        records = [r for r in records if r.get("status") == status_filter]
    return {"success": True, "queries": records[:limit], "total": len(records)}


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
    """Return saved queries for the caller's workspace, starred first then newest-first."""
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        records = [r for r in _saved_queries_store if _in_workspace(r, wsid)]
    records.sort(key=lambda r: (0 if r.get("starred") else 1, -float(r.get("created_ts", 0))))
    return {"success": True, "queries": records, "total": len(records)}


@router.post("/saved-queries")
async def create_saved_query(payload: SavedQueryCreate, request: Request):
    """Create a saved query in the caller's workspace."""
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
    with _saved_queries_lock:
        _saved_queries_store.insert(0, record)
        if len(_saved_queries_store) > 500:
            del _saved_queries_store[500:]
    return {"success": True, "query": record}


@router.patch("/saved-queries/{query_id}")
async def update_saved_query(query_id: str, payload: SavedQueryUpdate, request: Request):
    """Rename or toggle star on a saved query in the caller's workspace."""
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        record = next(
            (r for r in _saved_queries_store if r["id"] == query_id and _in_workspace(r, wsid)),
            None,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Saved query not found")
        if payload.name is not None:
            new_name = payload.name.strip()
            if not new_name:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            record["name"] = new_name
        if payload.starred is not None:
            record["starred"] = bool(payload.starred)
        record["updated_at"] = datetime.now().isoformat()
    return {"success": True, "query": record}


@router.delete("/saved-queries/{query_id}")
async def delete_saved_query(query_id: str, request: Request):
    """Delete a saved query from the caller's workspace."""
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        before = len(_saved_queries_store)
        _saved_queries_store[:] = [
            r for r in _saved_queries_store
            if not (r["id"] == query_id and _in_workspace(r, wsid))
        ]
        if len(_saved_queries_store) == before:
            raise HTTPException(status_code=404, detail="Saved query not found")
    # Also revoke any share tokens pointing at this query.
    with _share_tokens_lock:
        for tok, qid in list(_share_tokens_store.items()):
            if qid == query_id:
                del _share_tokens_store[tok]
    return {"success": True, "id": query_id}


# ── Saved-query share tokens (public read-only) ─────────────────────

@router.post("/saved-queries/{query_id}/share")
async def create_share_link(query_id: str, request: Request):
    """Mint (or re-mint) a share token for a saved query. Returns an
    opaque URL-safe token; the public endpoint is
    ``GET /public/saved-queries/{token}`` and requires no workspace header."""
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        exists = any(r["id"] == query_id and _in_workspace(r, wsid) for r in _saved_queries_store)
    if not exists:
        raise HTTPException(status_code=404, detail="Saved query not found")

    with _share_tokens_lock:
        # Re-use an existing token for idempotency.
        existing = next((tok for tok, qid in _share_tokens_store.items() if qid == query_id), None)
        if existing is not None:
            token = existing
        else:
            token = secrets.token_urlsafe(24)
            _share_tokens_store[token] = query_id
    return {"success": True, "token": token, "query_id": query_id}


@router.delete("/saved-queries/{query_id}/share")
async def revoke_share_link(query_id: str, request: Request):
    """Revoke all share tokens pointing at this query."""
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        exists = any(r["id"] == query_id and _in_workspace(r, wsid) for r in _saved_queries_store)
    if not exists:
        raise HTTPException(status_code=404, detail="Saved query not found")
    revoked = 0
    with _share_tokens_lock:
        for tok, qid in list(_share_tokens_store.items()):
            if qid == query_id:
                del _share_tokens_store[tok]
                revoked += 1
    return {"success": True, "query_id": query_id, "revoked": revoked}


@router.get("/public/saved-queries/{token}")
async def read_shared_query(token: str):
    """Public read-only endpoint for a shared saved query.

    Intentionally bypasses the workspace header — anyone with the token
    sees the query's name, SQL, and prompt. No write paths are exposed.
    """
    with _share_tokens_lock:
        query_id = _share_tokens_store.get(token)
    if query_id is None:
        raise HTTPException(status_code=404, detail="Share link is invalid or has been revoked")
    with _saved_queries_lock:
        record = next((r for r in _saved_queries_store if r["id"] == query_id), None)
    if record is None:
        # Token references a deleted query — clean up.
        with _share_tokens_lock:
            _share_tokens_store.pop(token, None)
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
    if payload.interval not in ("hourly", "daily", "weekly"):
        raise HTTPException(status_code=400, detail="interval must be hourly | daily | weekly")
    schedule = payload.dict()
    next_run = _compute_next_run(schedule)
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        record = next(
            (r for r in _saved_queries_store if r["id"] == query_id and _in_workspace(r, wsid)),
            None,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Saved query not found")
        record["schedule"] = schedule
        record["next_run_at"] = next_run
        record["updated_at"] = datetime.now().isoformat()
    return {"success": True, "query": record}


@router.delete("/saved-queries/{query_id}/schedule")
async def clear_saved_query_schedule(query_id: str, request: Request):
    """Remove the schedule on a saved query."""
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        record = next(
            (r for r in _saved_queries_store if r["id"] == query_id and _in_workspace(r, wsid)),
            None,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Saved query not found")
        record.pop("schedule", None)
        record["next_run_at"] = None
        record["updated_at"] = datetime.now().isoformat()
    return {"success": True, "query": record}


@router.get("/saved-queries/{query_id}/runs")
async def list_saved_query_runs(query_id: str, request: Request, limit: int = 20):
    """Return recent scheduled runs (newest first) for a saved query."""
    wsid = current_workspace_id(request)
    with _saved_queries_lock:
        exists = any(r["id"] == query_id and _in_workspace(r, wsid) for r in _saved_queries_store)
    if not exists:
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
    """Iterate saved queries and fire any whose next_run_at has passed."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    with _saved_queries_lock:
        due: List[Dict[str, Any]] = []
        for record in _saved_queries_store:
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

        # Advance next_run_at from *now* so we don't backfire past runs.
        with _saved_queries_lock:
            live = next((r for r in _saved_queries_store if r["id"] == query_id), None)
            if live is not None:
                live["last_run_at"] = entry["completed_at"]
                live["next_run_at"] = _compute_next_run(live.get("schedule") or {})


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
