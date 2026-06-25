"""
Chat Router
============
All chat-related endpoints: unified NL→SQL→Execute→Visualize pipeline,
chat history management.
"""

import asyncio
import json
import os
import pathlib
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.base import AgentContext
from agents.commander import ErrorEvent, run_commander
from agents.commander_tools import build_default_registry
from agents.langgraph_orchestrator import run_orchestrator
from agents.specialists.intent_agent import IntentAgent
from api_gateway.persistence import insert_chat_message, list_chat_messages
from shared.config import settings
from shared.data_utils import build_schema_context_cached
from shared.duckdb_factory import new_connection
from shared.llm_provider import get_llm
from shared.logging_config import get_logger
from shared.observability import CHAT_REQUESTS
from shared.sql_identifiers import quote_identifier

from .workspaces import _request_tenant, current_workspace_id, tenant_upload_dir

# Commander Core (Subsystem A): the streaming POST /chat/stream loop, gated by
# settings.commander_enabled. Built once; the registry is stateless.
_COMMANDER_REGISTRY = build_default_registry()
_STREAM_SENTINEL = object()

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


class ChatStreamRequest(BaseModel):
    """Commander streaming chat (POST /chat/stream)."""
    message: str
    context: Optional[str] = None
    session_id: Optional[str] = None
    uploaded_file: Optional[str] = None


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
    # Commander mode: a side-effect the chat performed that the UI should
    # reflect (e.g. {"type": "pipeline_created", "pipeline_id": ..., "name": ...}).
    action: Optional[Dict[str, Any]] = None


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


# ── Commander helpers (audit intent) ─────────────────────────────────

_AMOUNT_NAME_HINTS = (
    "amount", "amt", "total", "value", "price", "cost", "revenue", "sales",
    "balance", "sum", "debit", "credit", "net", "gross", "pay",
)
_NUMERIC_TYPE_HINTS = ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL", "HUGEINT", "BIGINT")


def _is_numeric_type(t: Any) -> bool:
    return any(h in str(t or "").upper() for h in _NUMERIC_TYPE_HINTS)


def _looks_like_id(name: str) -> bool:
    """Identity/key columns are numeric but NOT monetary — auditing them (every
    value unique/sequential) trips every check and yields garbage. Exclude them."""
    n = name.lower()
    return (
        n in ("id", "idx", "index", "pk", "uuid", "guid", "year", "month", "day", "zip", "zipcode")
        or n.endswith("_id") or n.endswith("_key") or n.endswith("_code") or n.endswith("_no")
        or n.endswith("_uuid") or "uuid" in n or "guid" in n
    )


def _pick_amount_column(columns: List[Dict[str, Any]]) -> Optional[str]:
    """Choose the monetary column to audit: a numeric column named like an
    amount, else the first numeric non-identity column. Identity/key columns
    (…_id, uuid, year, …) are excluded — auditing them yields meaningless
    findings."""
    numeric = [
        c for c in columns
        if _is_numeric_type(c.get("type")) and not _looks_like_id(str(c.get("name", "")))
    ]
    named = next(
        (c["name"] for c in numeric if any(h in c["name"].lower() for h in _AMOUNT_NAME_HINTS)),
        None,
    )
    return named or (numeric[0]["name"] if numeric else None)


def _pick_audit_table(
    uploaded_file: Optional[str], message: str, all_tables: Dict[str, Any],
) -> Optional[str]:
    """Pick which table to audit: the explicit uploaded_file, else a table named
    in the message, else the table with the most numeric columns."""
    if not all_tables:
        return None
    if uploaded_file:
        stem = re.sub(r"[^A-Za-z0-9_]", "_", pathlib.PurePath(uploaded_file).stem)
        if stem in all_tables:
            return stem
    msg = message.lower()
    for tname in sorted(all_tables, key=len, reverse=True):
        if tname and tname.lower() in msg:
            return tname
    best = max(
        all_tables,
        key=lambda t: sum(1 for c in all_tables[t]["columns"] if _is_numeric_type(c.get("type"))),
    )
    return best if any(_is_numeric_type(c.get("type")) for c in all_tables[best]["columns"]) else None


def _forensic_findings(amounts: List[float]) -> List[Dict[str, Any]]:
    """Population-level forensic checks on a numeric column. Returns a finding
    ONLY for genuine anomalies (not one per row): Benford's-law conformity,
    duplicate concentration, 3σ outliers, round-number excess. Thresholds follow
    standard forensic-analytics practice, so clean data yields few/no findings."""
    import statistics
    from collections import Counter

    from agents.specialists.financial_auditor import _benford_first_digit_mad

    findings: List[Dict[str, Any]] = []
    n = len(amounts)

    mad, benford_n = _benford_first_digit_mad(amounts)
    if mad is not None and benford_n >= 50:
        if mad > 0.015:
            findings.append({"finding_id": "benford", "pcaob_standard": "AS 2401", "risk_level": "High",
                "description": f"Benford's-law nonconformity (first-digit MAD={mad:.4f} over {benford_n} values) — a classic indicator of fabricated or manipulated amounts.",
                "evidence_payload": {"test": "benford_first_digit", "mad": round(mad, 5), "n": benford_n}, "requires_human_review": True})
        elif mad > 0.012:
            findings.append({"finding_id": "benford", "pcaob_standard": "AS 2401", "risk_level": "Medium",
                "description": f"Marginal Benford's-law conformity (MAD={mad:.4f}); some amounts may not be naturally occurring.",
                "evidence_payload": {"test": "benford_first_digit", "mad": round(mad, 5), "n": benford_n}, "requires_human_review": True})

    counts = Counter(amounts)
    dup_rows = sum(c - 1 for c in counts.values() if c > 1)
    repeated = sum(1 for c in counts.values() if c > 1)
    if n and dup_rows / n > 0.4 and repeated >= 3:
        findings.append({"finding_id": "duplicates", "pcaob_standard": "AS 2401", "risk_level": "Medium",
            "description": f"High duplicate concentration: {dup_rows} duplicate values across {repeated} repeated amounts ({dup_rows/n:.0%} of rows).",
            "evidence_payload": {"test": "duplicate_amounts", "duplicate_rows": dup_rows, "repeated_values": repeated}, "requires_human_review": False})

    if n >= 8:
        mean = statistics.fmean(amounts)
        std = statistics.pstdev(amounts)
        if std > 0:
            outliers = [a for a in amounts if abs(a - mean) > 3 * std]
            if outliers:
                worst = max(outliers, key=lambda a: abs(a - mean))
                findings.append({"finding_id": "outliers", "pcaob_standard": "AS 2305",
                    "risk_level": "High" if len(outliers) <= max(3, int(0.02 * n)) else "Medium",
                    "description": f"{len(outliers)} value(s) beyond 3 standard deviations (largest: {worst:.2f}); material outliers warrant substantive testing.",
                    "evidence_payload": {"test": "z_score_outlier", "count": len(outliers), "max_value": worst, "mean": round(mean, 2), "std": round(std, 2)}, "requires_human_review": True})

    round_n = sum(1 for a in amounts if a and (a % 1000 == 0 or a % 100 == 0))
    if n and round_n / n > 0.25 and round_n >= 5:
        findings.append({"finding_id": "round_numbers", "pcaob_standard": "AS 2401", "risk_level": "Low",
            "description": f"{round_n} round-number amounts ({round_n/n:.0%}); an unusually high share can indicate estimates or manual entries.",
            "evidence_payload": {"test": "round_number", "count": round_n}, "requires_human_review": False})

    return findings


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

    # Prefer the FULL multi-table schema so cross-table joins resolve against
    # real columns. A previous "focus on a single table" optimization made the
    # model hallucinate columns on join partners (e.g. salesorder.amount when
    # the real column is quantity) because it never saw the other table. Only
    # fall back to a focused subset when the full context would blow the token
    # budget — and then include the focused table's RELATED tables too, so
    # joins across that subset still have real columns.
    full_table_schemas = {
        name: [c["name"] for c in info["columns"]]
        for name, info in all_tables.items()
    }
    full_context_text = schema_result["context_text"]
    max_schema_chars = int(os.getenv("AURA_MAX_SCHEMA_CONTEXT_CHARS", "16000"))

    if not all_tables:
        table_schemas = {}
        schema_context = "No tables available. User needs to upload a file first."
    elif len(full_context_text) <= max_schema_chars or not focus_name:
        # Small enough (the common case) — give the model every table.
        table_schemas = full_table_schemas
        schema_context = full_context_text
    else:
        # Over budget AND the user named a table: focus on it + its directly
        # related tables so joins across the subset still have real columns.
        from shared.data_utils import _format_context_for_llm
        focused_rels = [
            r for r in schema_result["relationships"]
            if r.get("from_table") == focus_name or r.get("to_table") == focus_name
        ]
        related = {focus_name}
        for r in focused_rels:
            related.add(r.get("from_table"))
            related.add(r.get("to_table"))
        focused_tables = {n: all_tables[n] for n in related if n in all_tables}
        schema_context = _format_context_for_llm(focused_tables, focused_rels)
        table_schemas = {
            n: [c["name"] for c in all_tables[n]["columns"]] for n in focused_tables
        }
        logger.info(
            "chat: schema over budget (%d chars) — focused on %s + %d related (of %d available)",
            len(full_context_text), focus_name, len(focused_tables) - 1, len(all_tables),
        )

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

    if intent == "pipeline":
        # Commander mode: build a real ETL pipeline from the request and
        # persist it so it appears in the ETL Pipelines tab — the chat
        # dictates the app, it doesn't only answer questions.
        con.close()
        try:
            from api_gateway.persistence import save_pipeline
            from api_gateway.routers.pipelines import _get_generator
            gen = _get_generator()
            upload_dir = tenant_upload_dir(http_request)
            data_exts = {".csv", ".parquet", ".json", ".xlsx", ".tsv"}
            available_files = (
                [f for f in sorted(os.listdir(upload_dir))
                 if os.path.splitext(f)[1].lower() in data_exts]
                if os.path.isdir(upload_dir) else []
            )
            pipeline = await gen.generate(
                prompt=message, available_files=available_files, schema_context=None,
            )
            saved = await save_pipeline({
                "id": pipeline.id,
                "workspace_id": current_workspace_id(http_request),
                "name": pipeline.name,
                "description": pipeline.description,
                "definition": pipeline.model_dump(),
                "status": pipeline.status.value,
                "source_label": pipeline.source.label(),
                "sink_type": pipeline.sink.type.value,
                "step_count": len(pipeline.steps or []),
                "tags": pipeline.tags or [],
                "created_at": pipeline.created_at,
                "updated_at": pipeline.updated_at,
            })
            CHAT_REQUESTS.labels(status="pipeline").inc()
            return ChatResponse(
                status="PipelineCreated",
                job_id=f"job_{session_id}",
                message=(
                    f"Built and saved the pipeline \"{saved['name']}\" "
                    f"({pipeline.source.label()} → {pipeline.sink.type.value}, "
                    f"{len(pipeline.steps or [])} transform step(s)). "
                    f"Open it in ETL Pipelines to run or edit it."
                ),
                execution_time_ms=round((time.perf_counter() - t0) * 1000, 1),
                available_tables=list(table_schemas.keys()),
                action={"type": "pipeline_created", "pipeline_id": saved["id"], "name": saved["name"]},
            )
        except Exception as exc:
            CHAT_REQUESTS.labels(status="pipeline_error").inc()
            logger.warning("chat: pipeline creation failed: %s", exc)
            return ChatResponse(
                status="Error",
                job_id=f"job_{session_id}",
                message=f"I couldn't build that pipeline: {str(exc)[:200]}",
                execution_time_ms=round((time.perf_counter() - t0) * 1000, 1),
                available_tables=list(table_schemas.keys()),
            )

    if intent == "audit":
        # Commander mode: run the REAL forensic auditor on the chosen dataset's
        # monetary column and return a signed certificate the UI can open.
        target = _pick_audit_table(request.uploaded_file, message, all_tables)
        amount_col = _pick_amount_column(all_tables[target]["columns"]) if target else None
        if not target or not amount_col:
            con.close()
            CHAT_REQUESTS.labels(status="audit_error").inc()
            return ChatResponse(
                status="Error",
                job_id=f"job_{session_id}",
                message=(
                    "I need a dataset with a numeric/amount column to audit. "
                    "Upload one (or name it in your request) and try again."
                ),
                execution_time_ms=round((time.perf_counter() - t0) * 1000, 1),
                available_tables=list(table_schemas.keys()),
            )
        try:
            def _read_amounts() -> List[float]:
                cur = con.execute(
                    f"SELECT {quote_identifier(amount_col)} FROM {quote_identifier(target)}"
                )
                out: List[float] = []
                for (v,) in cur.fetchall():
                    if v is None:
                        continue
                    try:
                        out.append(float(v))
                    except (TypeError, ValueError):
                        continue
                return out

            amounts = await asyncio.to_thread(_read_amounts)
            con.close()
            if not amounts:
                CHAT_REQUESTS.labels(status="audit_error").inc()
                return ChatResponse(
                    status="Error", job_id=f"job_{session_id}",
                    message=f'No numeric values found in "{target}"."{amount_col}" to audit.',
                    execution_time_ms=round((time.perf_counter() - t0) * 1000, 1),
                    available_tables=list(table_schemas.keys()),
                )
            entries = [
                {"id": f"{target}-{i}", "account": target, "amount": a}
                for i, a in enumerate(amounts)
            ]

            from counterfactual_service.financial_report import (
                build_completion_document,
                client_view,
                dataset_fingerprint,
                sign_and_persist,
            )
            findings = await asyncio.to_thread(_forensic_findings, amounts)
            doc = build_completion_document(
                str(tenant), findings,
                dataset_fingerprint(entries, [], [], entries),
                0.0,
            )
            view = client_view(sign_and_persist(doc))
            rhash = view.get("record_hash", "")
            n = view.get("n_findings", len(findings))
            sig = view.get("signature_status", "unsigned")

            def _risk(f: Any) -> str:
                return (f.get("risk_level") if isinstance(f, dict) else getattr(f, "risk_level", None)) or "Low"

            highs = sum(1 for f in findings if _risk(f) == "High")
            meds = sum(1 for f in findings if _risk(f) == "Medium")
            lows = n - highs - meds
            CHAT_REQUESTS.labels(status="audit").inc()
            return ChatResponse(
                status="AuditCompleted",
                job_id=f"job_{session_id}",
                message=(
                    f'Forensic audit of "{target}" on "{amount_col}" ({len(amounts)} values): '
                    f"{n} finding(s) — {highs} high, {meds} medium, {lows} low risk. "
                    f"Certificate {sig}. Open the signed certificate to review the evidence."
                ),
                execution_time_ms=round((time.perf_counter() - t0) * 1000, 1),
                available_tables=list(table_schemas.keys()),
                action={"type": "audit_created", "record_hash": rhash, "n_findings": n, "signature_status": sig},
            )
        except Exception as exc:
            try:
                con.close()
            except Exception:
                pass
            CHAT_REQUESTS.labels(status="audit_error").inc()
            logger.warning("chat: audit failed: %s", exc)
            return ChatResponse(
                status="Error", job_id=f"job_{session_id}",
                message=f"I couldn't run that audit: {str(exc)[:200]}",
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


async def _build_commander_session(http_request: Request, req: "ChatStreamRequest"):
    """Build a tenant-scoped DuckDB connection + schema-context text, the same
    way chat_endpoint does. ``build_schema_context_cached`` is async, so this
    runs in the request coroutine; the returned ``con`` is then used ONLY by
    the worker thread (access stays serialised — never concurrent)."""
    tenant = _request_tenant(http_request)
    con = new_connection()
    # Loads the tenant's datasets into `con` (side effect) — but we pass the
    # commander only the compact table NAMES, not the verbose rich context.
    # The model fetches columns on demand via describe_table, keeping each LLM
    # turn small (the rich dump was ~3.2k tokens → provider rate-limit latency).
    schema_result = await build_schema_context_cached(con, tenant, use_llm=True)
    table_names = list(schema_result.get("tables", {}).keys())
    context_text = ("Loaded tables: " + ", ".join(table_names)) if table_names else "No tables loaded yet."
    if req.context:
        context_text = f"{req.context}\n\n{context_text}"
    return con, context_text, (tenant or "default")


@router.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest, http_request: Request) -> StreamingResponse:
    """Commander streaming chat (coexists with POST /chat). 404 unless the
    AURA_COMMANDER_ENABLED flag is on. Streams typed SSE events from the
    agentic tool-loop; the blocking loop runs in a worker thread bridged to
    the async response via an asyncio.Queue."""
    if not settings.commander_enabled:
        raise HTTPException(status_code=404, detail="commander disabled")
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    con, schema_context, tenant = await _build_commander_session(http_request, req)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _worker() -> None:
        try:
            for ev in run_commander(
                message, tenant=tenant, schema_context=schema_context,
                registry=_COMMANDER_REGISTRY, llm=get_llm(), con=con,
            ):
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception as exc:  # never lose the stream on an unexpected error
            loop.call_soon_threadsafe(queue.put_nowait, ErrorEvent(kind="internal", message=str(exc)))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_SENTINEL)
            try:
                con.close()
            except Exception:
                pass

    async def _sse():
        worker = loop.run_in_executor(None, _worker)
        try:
            while True:
                ev = await queue.get()
                if ev is _STREAM_SENTINEL:
                    break
                if await http_request.is_disconnected():
                    break
                yield ev.to_sse()
        finally:
            worker.cancel()

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
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
