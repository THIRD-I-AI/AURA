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

import duckdb
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.base import AgentContext
from agents.executor import DAGExecutor
from agents.planner import ExecutionPlan, TaskNode, TaskType
from agents.specialists.intent_agent import IntentAgent
from shared.data_utils import build_schema_context_cached
from shared.logging_config import get_logger
from shared.observability import CHAT_REQUESTS

logger = get_logger("aura.api_gateway.chat")

router = APIRouter(tags=["Chat"])


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


# ── In-memory chat history ───────────────────────────────────────────

import threading
import uuid as _uuid
from datetime import datetime

_chat_history_lock = threading.Lock()
_chat_history_store: Dict[str, List[Dict[str, Any]]] = {}  # session_id → messages


# ── Shared helpers (imported by other routers too) ───────────────────

def _track_query(prompt: str, sql: str, status: str, rows: int, execution_time_ms: float):
    """Record a query execution in the in-memory store.
    Imported from queries router at runtime to avoid circular imports.
    """
    try:
        from api_gateway.routers.queries import track_query
        track_query(prompt, sql, status, rows, execution_time_ms)
    except Exception:
        pass


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """Unified chat endpoint: NL → SQL → Execute → Visualize in one call.

    1. Discovers available tables from uploaded files (DuckDB) with smart header detection.
    2. Sends rich schema context (columns, types, sample data, relationships) to LLM.
    3. Auto-executes the SQL on DuckDB.
    4. Returns data + generated SQL + chart suggestion.
    """
    t0 = time.time()
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    # ── Step 1: Smart-load all tables with header inference ─────────
    base = pathlib.Path(__file__).resolve().parent.parent.parent
    upload_dirs = [
        base / "data" / "uploads",
        base / "api_gateway" / "uploads",
        base.parent / "uploads",
    ]

    con = duckdb.connect(":memory:")
    schema_result = await build_schema_context_cached(con, upload_dirs, use_llm=True)
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
            execution_time_ms=round((time.time() - t0) * 1000, 1),
            available_tables=list(table_schemas.keys()),
        )

    # 2. Build explicit execution plan for SQL queries
    plan = ExecutionPlan(
        plan_id=session_id,
        user_prompt=message,
        summary="Unified SQL Pipeline",
        tasks=[
            TaskNode(id="t1", task_type=TaskType.GENERATE_SQL, description=f"Generate SQL to answer: {message}", agent_name="SQLGeneratorAgent", depends_on=[]),
            TaskNode(id="t2", task_type=TaskType.EXECUTE_SQL, description="Execute Query", agent_name="ExecutionAgent", depends_on=["t1"], parameters={"duckdb_con": con}),
            TaskNode(id="t3", task_type=TaskType.TRANSFORM, description="Suggest Chart", agent_name="VisualizationAgent", depends_on=["t2"]),
            TaskNode(id="t4", task_type=TaskType.TRANSFORM, description="Analyze Output", agent_name="AnalysisAgent", depends_on=["t1", "t2", "t3"]),
        ],
    )

    # 3. Execute the DAG!
    executor = DAGExecutor()
    executor.progress_cb = console_cb

    report = await executor.execute(
        plan,
        user_prompt=message,
        schema_context={"tables": table_schemas, "rich_context": full_context},
    )

    # Extract results from plan execution
    generated_sql: Optional[str] = None
    error_message = report.summary if not report.success else None
    execution_result = ExecutionResult()

    for task_id, task_result in report.task_results.items():
        if not task_result.succeeded and task_id == "t1":
            error_message = f"SQL Generation failed: {task_result.error}"
        if not task_result.succeeded and task_id == "t2":
            execution_result.error = f"Execution failed: {task_result.error}"

        task_output = task_result.output
        if task_output.get("sql"):
            generated_sql = task_output["sql"]
        if task_output.get("explanation"):
            execution_result.sql_explanation = task_output["explanation"]
        if "records" in task_output:
            execution_result.success = True
            execution_result.data = task_output["records"]
            execution_result.columns = task_output["columns"]
            execution_result.rows = task_output["rows"]
            execution_result.row_count = len(task_output["records"])
        if "chart_spec" in task_output:
            execution_result.chart_spec = task_output["chart_spec"]
        if "conclusion" in task_output:
            execution_result.conclusion = task_output["conclusion"]

    con.close()
    elapsed_ms = (time.time() - t0) * 1000

    # ── Track query in server-side history ──────────────────────────
    if generated_sql:
        q_status = "success" if execution_result.success else "error"
        _track_query(message, generated_sql, q_status, execution_result.row_count, elapsed_ms)

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
async def get_chat_history(session_id: str) -> List[ChatHistoryEntry]:
    """Get chat history for a session."""
    with _chat_history_lock:
        messages = _chat_history_store.get(session_id, [])
    return [ChatHistoryEntry(**m) for m in messages]


@router.post("/chat/history/{session_id}", response_model=SaveChatResponse)
async def save_chat_message(session_id: str, payload: Dict[str, Any]) -> SaveChatResponse:
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
    return SaveChatResponse(success=True, id=msg["id"])
