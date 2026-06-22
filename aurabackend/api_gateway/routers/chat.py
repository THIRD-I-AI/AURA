"""
Chat Router
============
All chat-related endpoints: unified NL→SQL→Execute→Visualize pipeline,
chat history management.
"""

import json
import os
import pathlib
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agents.base import AgentContext
from agents.langgraph_orchestrator import run_orchestrator
from agents.specialists.intent_agent import IntentAgent
from api_gateway.persistence import insert_chat_message, list_chat_messages
from shared.data_utils import build_schema_context_cached
from shared.duckdb_factory import new_connection
from shared.logging_config import get_logger
from shared.observability import CHAT_REQUESTS

from .workspaces import _request_tenant, current_workspace_id, tenant_upload_dir

logger = get_logger("aura.api_gateway.chat")

router = APIRouter(tags=["Chat"])


# Raw provider exceptions (e.g. a multi-line Gemini 429 quota dump, or a
# stack-tracey internal error) must never reach the UI verbatim. Map the
# common, *expected* failure signatures to crisp, actionable text, and
# hard-cap anything else so a wall of provider text can't leak to the user.
_RATE_LIMIT_MARKERS = (
    "rate limit", "rate/size", "quota", "429",
    "resource_exhausted", "too large", "413",
)
_NO_LLM_MARKERS = (
    "no llm provider", "llm provider available",
    "no provider", "ollama",
)


def _humanize_pipeline_error(message: Optional[str]) -> str:
    low = (message or "").lower()
    if any(m in low for m in _RATE_LIMIT_MARKERS):
        return (
            "The AI service is temporarily rate-limited. "
            "Please wait a few seconds and try again."
        )
    if any(m in low for m in _NO_LLM_MARKERS):
        return (
            "No AI model is configured on the server. Set GROQ_API_KEY or "
            "GEMINI_API_KEY (or start a local Ollama) and retry."
        )
    msg = (message or "").strip() or "unknown error"
    return msg if len(msg) <= 240 else msg[:237] + "…"


# ── Models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = None
    session_id: Optional[str] = None
    uploaded_file: Optional[str] = None
    columns: Optional[List[str]] = None
    auto_execute: bool = True


class ExecutionResult(BaseModel):
    """Result of running the generated SQL against DuckDB."""
    success: bool = False
    data: List[Dict[str, Any]] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    row_count: int = 0
    chart_spec: Optional[Dict[str, Any]] = None
    conclusion: Optional[str] = None
    sql_explanation: Optional[str] = None
    error: Optional[str] = None


class ChatMetadata(BaseModel):
    timestamp: str
    tables_loaded: int


class ChatResponse(BaseModel):
    status: str
    job_id: str
    final_query: Optional[str] = None
    message: Optional[str] = None  # only for conversational replies
    execution_time_ms: float
    available_tables: List[str] = Field(default_factory=list)
    metadata: Optional[ChatMetadata] = None
    error_message: Optional[str] = None
    execution_result: Optional[ExecutionResult] = None


class ChatHistoryEntry(BaseModel):
    id: str
    type: str
    content: str
    timestamp: str
    metadata: Optional[Dict[str, Any]] = None


class SaveChatResponse(BaseModel):
    success: bool
    id: str


# ── Chat history ─────────────────────────────────────────────────────
# Durable + tenant-scoped (S50): persisted in gateway_chat_messages,
# isolated per tenant. See api_gateway/persistence.py.

from datetime import datetime

# ── Shared helpers (imported by other routers too) ───────────────────

async def _track_query(prompt: str, sql: str, status: str, rows: int, execution_time_ms: float):
    """Record a query execution in the persistence layer.

    Sprint P-1 made the underlying ``track_query`` async (it now writes
    to the gateway's SQL-backed history table). This wrapper preserves
    the historical fire-and-forget semantics by swallowing any
    exception — tracking failures must never break the chat path.
    """
    try:
        from api_gateway.routers.queries import track_query
        await track_query(prompt, sql, status, rows, execution_time_ms)
    except Exception:
        pass


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat_endpoint(request: ChatRequest, http_request: Request) -> ChatResponse:
    """Unified chat endpoint: NL → SQL → Execute → Visualize in one call.

    1. Discovers available tables from uploaded files (DuckDB) with smart header detection.
    2. Sends rich schema context (columns, types, sample data, relationships) to LLM.
    3. Auto-executes the SQL on DuckDB.
    4. Returns data + generated SQL + chart suggestion.
    """
    t0 = time.perf_counter()
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    # ── Step 1: Smart-load all tables with header inference ─────────
    tenant = _request_tenant(http_request)
    con = new_connection()
    schema_result = await build_schema_context_cached(con, tenant, use_llm=True)
    all_tables = schema_result["tables"]

    # ── Focus the schema context on a single table if the user clearly
    # asked about one. Without this, a directory with many uploads
    # (e.g. a 70-col World Bank CSV next to a multi-GB metadata file)
    # would push the prompt over AURA_MAX_TOKENS_PER_REQUEST. Priority:
    # explicit uploaded_file from the request, then any table name
    # mentioned in the message itself.
    focus_name: Optional[str] = None
    if request.uploaded_file:
        stem = pathlib.PurePath(request.uploaded_file).stem
        candidate = re.sub(r"[^A-Za-z0-9_]", "_", stem)
        if candidate in all_tables:
            focus_name = candidate
    if focus_name is None:
        # Longest-first so a shorter name that's a substring of a longer
        # one doesn't shadow the user's actual reference.
        for tname in sorted(all_tables, key=len, reverse=True):
            if tname and tname in message:
                focus_name = tname
                break

    if focus_name:
        from shared.data_utils import _format_context_for_llm
        focused_tables = {focus_name: all_tables[focus_name]}
        focused_rels = [
            r for r in schema_result["relationships"]
            if r.get("from_table") == focus_name or r.get("to_table") == focus_name
        ]
        schema_context = _format_context_for_llm(focused_tables, focused_rels)
        table_schemas = {focus_name: [c["name"] for c in all_tables[focus_name]["columns"]]}
        logger.info("chat: focused schema context on table %s (of %d available)", focus_name, len(all_tables))
    else:
        table_schemas = {
            name: [c["name"] for c in info["columns"]]
            for name, info in all_tables.items()
        }
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
    # 1. Check Intent First (Standalone early exit)
    intent_agent = IntentAgent()
    ctx = AgentContext(
        user_prompt=message,
        task_description="Determine intent.",
        schema_context=table_schemas,
    )

    async def console_cb(agent_name: str, msg: str, pct: float):
        logger.info(f"[{agent_name}] {msg}")

    intent_agent.set_progress_callback(console_cb)
    intent_result = await intent_agent.execute(ctx)
    intent = intent_result.output.get("intent") if intent_result.succeeded else "sql"

    if intent == "conversation":
        con.close()
        CHAT_REQUESTS.labels(status="conversational").inc()
        return ChatResponse(
            status="Conversational",
            job_id=f"job_{session_id}",
            message=intent_result.output.get("message", "Hello! How can I help you today?"),
            execution_time_ms=round((time.perf_counter() - t0) * 1000, 1),
            available_tables=list(table_schemas.keys()),
        )

    # 2. Run the canonical NL→SQL→Execute→Visualize→Analyze path through
    #    the LangGraph orchestrator. ``skip_planner=True`` because chat
    #    has already chosen the path; the planner is only useful when
    #    the agent flow is open-ended (e.g. /agent/execute).
    state = await run_orchestrator(
        message,
        session_id=session_id,
        schema_context={"tables": table_schemas, "rich_context": full_context},
        skip_planner=True,
        duckdb_con=con,
    )

    # 3. Map typed orchestrator state back into the ChatResponse shape.
    generated_sql: Optional[str] = state.sql.sql if state.sql else None
    execution_result = ExecutionResult()
    error_message: Optional[str] = None

    if state.errors:
        # Surface the FIRST blocking error — downstream errors are usually
        # cascade effects of the first failure (sql gen → execution).
        first = state.errors[0]
        human = _humanize_pipeline_error(first.message)
        if first.node == "sql_gen":
            error_message = f"SQL generation failed: {human}"
        elif first.node == "execution":
            execution_result.error = f"Execution failed: {human}"
        else:
            error_message = f"{first.node} failed: {human}"

    if state.sql and state.sql.explanation:
        execution_result.sql_explanation = state.sql.explanation

    if state.execution and state.execution.row_count > 0:
        execution_result.success = True
        execution_result.data = state.execution.records
        execution_result.columns = state.execution.columns
        execution_result.rows = state.execution.rows
        execution_result.row_count = state.execution.row_count

    if state.visualization and state.visualization.chart:
        execution_result.chart_spec = state.visualization.chart.model_dump()

    if state.analysis and state.analysis.conclusion:
        execution_result.conclusion = state.analysis.conclusion

    con.close()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # ── Track query in server-side history ──────────────────────────
    if generated_sql:
        q_status = "success" if execution_result.success else "error"
        await _track_query(message, generated_sql, q_status, execution_result.row_count, elapsed_ms)

    CHAT_REQUESTS.labels(status="ok" if error_message is None else "error").inc()
    return ChatResponse(
        status="Success" if error_message is None else "Error",
        job_id=f"job_{session_id}",
        final_query=generated_sql,
        execution_time_ms=round(elapsed_ms, 1),
        available_tables=list(table_schemas.keys()),
        metadata=ChatMetadata(
            timestamp=datetime.now().isoformat(),
            tables_loaded=len(table_schemas),
        ),
        error_message=error_message,
        execution_result=execution_result if (execution_result.success or execution_result.error or execution_result.sql_explanation) else None,
    )


@router.get("/chat/history/{session_id}", response_model=List[ChatHistoryEntry])
async def get_chat_history(session_id: str, http_request: Request) -> List[ChatHistoryEntry]:
    """Tenant-scoped chat history for a session.

    SECURITY (S50): scopes on the caller's tenant (``current_workspace_id``),
    closing the pre-S50 hole where this read had no auth/tenant check and a
    guessed session id leaked another tenant's history."""
    workspace_id = current_workspace_id(http_request)
    messages = await list_chat_messages(workspace_id, session_id)
    return [ChatHistoryEntry(**m) for m in messages]


@router.post("/chat/history/{session_id}", response_model=SaveChatResponse)
async def save_chat_message(
    session_id: str, payload: Dict[str, Any], http_request: Request,
) -> SaveChatResponse:
    """Append a tenant-scoped chat message to durable session history."""
    workspace_id = current_workspace_id(http_request)
    saved = await insert_chat_message(workspace_id, {
        "id": payload.get("id"),
        "session_id": session_id,
        "type": payload.get("type", "user"),
        "content": payload.get("content", ""),
        "timestamp": payload.get("timestamp"),
        "metadata": payload.get("metadata"),
    })
    return SaveChatResponse(success=True, id=saved["id"])
