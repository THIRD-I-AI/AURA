"""
Agent API Router
================
FastAPI router that exposes the agentic DE framework as REST + SSE endpoints.

POST /agent/execute          — Submit a single prompt, get full execution report
POST /agent/execute/stream   — Same but streams progress via Server-Sent Events
POST /agent/plan             — Only generate the plan (no execution)
GET  /agent/tools            — List available tools
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.base import AgentContext, AgentStatus
from agents.executor import DAGExecutor
from agents.memory import AgentMemory
from agents.planner import PlannerAgent
from agents.tool_registry import ToolRegistry
from agents.tools import ingest_and_profile, register_all_tools
from shared.streaming_manager import TOPIC_AGENT, StreamEvent, streaming_manager

router = APIRouter(prefix="/agent", tags=["Agentic DE"])


# ── Request / Response models ─────────────────────────────────────────

class AgentExecuteRequest(BaseModel):
    prompt: str = Field(..., description="Natural-language DE task")
    files: List[str] = Field(default_factory=list, description="Uploaded file paths")
    connection: Optional[Dict[str, Any]] = Field(default=None, description="DB connection info")
    schema_context: Optional[Dict[str, Any]] = Field(default=None, description="Pre-loaded schema")
    execute_sql: bool = Field(default=False, description="Actually execute generated SQL")


class AgentPlanResponse(BaseModel):
    plan_id: str
    summary: str
    tasks: List[Dict[str, Any]]


class AgentExecuteResponse(BaseModel):
    success: bool
    summary: str
    duration_ms: float
    tasks: Dict[str, Any]
    skipped: List[str]


# ── Helpers ───────────────────────────────────────────────────────────

def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_all_tools(registry)
    return registry


async def _enrich_schema_from_files(
    files: List[str],
    existing_schema: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """If files are provided but no schema_context, auto-profile them."""
    schema = dict(existing_schema) if existing_schema else {}
    if not files or schema:
        return schema

    for fp in files:
        try:
            profile = await ingest_and_profile(file_path=fp)
            if profile.get("status") == "success":
                schema[fp] = {
                    "columns": profile.get("columns", []),
                    "dtypes": profile.get("dtypes", {}),
                    "rows": profile.get("rows", 0),
                }
        except Exception:
            pass  # profiling is best-effort
    return schema


# ── Routes ────────────────────────────────────────────────────────────

@router.post("/plan", response_model=AgentPlanResponse)
async def create_plan(req: AgentExecuteRequest):
    """Generate an execution plan without running it."""
    planner = PlannerAgent()
    ctx = AgentContext(
        user_prompt=req.prompt,
        task_description=req.prompt,
        session_id=str(uuid.uuid4()),
        files=req.files,
        connection=req.connection or {},
        schema_context=req.schema_context or {},
    )
    result = await planner.execute(ctx)

    if result.status != AgentStatus.SUCCESS or "plan" not in result.artifacts:
        raise HTTPException(status_code=500, detail=result.error or "Planning failed")

    plan = result.artifacts["plan"]
    return AgentPlanResponse(
        plan_id=plan.plan_id,
        summary=plan.summary,
        tasks=[t.to_dict() if hasattr(t, "to_dict") else _task_dict(t) for t in plan.tasks],
    )


@router.post("/execute", response_model=AgentExecuteResponse)
async def execute_prompt(req: AgentExecuteRequest):
    """Plan + execute the full DAG synchronously.  Returns when done."""
    registry = _make_registry()
    memory = AgentMemory()

    # Auto-enrich schema from files if none provided
    schema = await _enrich_schema_from_files(req.files, req.schema_context)

    # 1. Plan
    planner = PlannerAgent()
    ctx = AgentContext(
        user_prompt=req.prompt,
        task_description=req.prompt,
        session_id=str(uuid.uuid4()),
        files=req.files,
        connection=req.connection or {},
        schema_context=schema,
        metadata={"execute": req.execute_sql},
    )
    plan_result = await planner.execute(ctx)
    if plan_result.status != AgentStatus.SUCCESS or "plan" not in plan_result.artifacts:
        raise HTTPException(status_code=500, detail=plan_result.error or "Planning failed")

    plan = plan_result.artifacts["plan"]

    # 2. Execute
    executor = DAGExecutor(tool_registry=registry, memory=memory)
    report = await executor.execute(
        plan=plan,
        user_prompt=req.prompt,
        connection=req.connection or {},
        files=req.files,
        schema_context=schema,
    )

    return AgentExecuteResponse(**report.to_dict())


@router.post("/execute/stream")
async def execute_prompt_stream(req: AgentExecuteRequest):
    """Plan + execute with real-time SSE progress."""
    progress_queue: asyncio.Queue[str] = asyncio.Queue()

    async def _progress_cb(message: str, agent: str, pct: float) -> None:
        event = json.dumps({"agent": agent, "message": message, "progress": pct})
        await progress_queue.put(f"data: {event}\n\n")

    async def _generate():
        registry = _make_registry()
        memory = AgentMemory()

        # Auto-enrich schema from files
        schema = await _enrich_schema_from_files(req.files, req.schema_context)

        # Plan
        planner = PlannerAgent()
        planner.set_progress_callback(_progress_cb)

        ctx = AgentContext(
            user_prompt=req.prompt,
            task_description=req.prompt,
            session_id=str(uuid.uuid4()),
            files=req.files,
            connection=req.connection or {},
            schema_context=schema,
            metadata={"execute": req.execute_sql},
        )
        plan_result = await planner.execute(ctx)
        if plan_result.status != AgentStatus.SUCCESS or "plan" not in plan_result.artifacts:
            yield f"data: {json.dumps({'error': plan_result.error or 'Planning failed'})}\n\n"
            return

        plan = plan_result.artifacts["plan"]

        # Execute
        executor = DAGExecutor(
            tool_registry=registry,
            memory=memory,
            progress_cb=_progress_cb,
        )
        report = await executor.execute(
            plan=plan,
            user_prompt=req.prompt,
            connection=req.connection or {},
            files=req.files,
            schema_context=schema,
        )

        yield f"data: {json.dumps({'done': True, 'report': report.to_dict()})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


class AgentAsyncResponse(BaseModel):
    session_id: str
    topic: str


@router.post("/execute/async", response_model=AgentAsyncResponse)
async def execute_prompt_async(req: AgentExecuteRequest):
    """Kick off plan + execute in the background.

    Returns ``{session_id, topic}`` immediately. The caller subscribes to
    ``GET /stream/agent:{session_id}`` to receive live progress/complete/error
    events published through the universal streaming bus.
    """
    session_id = uuid.uuid4().hex

    async def _run() -> None:
        try:
            await streaming_manager.publish_progress(
                TOPIC_AGENT, session_id, "Enriching schema", 0.02,
                extra={"stage": "schema"},
            )
            registry = _make_registry()
            memory = AgentMemory()
            schema = await _enrich_schema_from_files(req.files, req.schema_context)

            async def _cb(message: str, agent: str, pct: float) -> None:
                try:
                    p = float(pct)
                except (TypeError, ValueError):
                    p = 0.0
                await streaming_manager.publish_progress(
                    TOPIC_AGENT, session_id, message, p,
                    extra={"agent": agent},
                )

            await streaming_manager.publish_progress(
                TOPIC_AGENT, session_id, "Planning", 0.05,
                extra={"stage": "planning"},
            )
            planner = PlannerAgent()
            planner.set_progress_callback(_cb)
            ctx = AgentContext(
                user_prompt=req.prompt,
                task_description=req.prompt,
                session_id=session_id,
                files=req.files,
                connection=req.connection or {},
                schema_context=schema,
                metadata={"execute": req.execute_sql},
            )
            plan_result = await planner.execute(ctx)
            if plan_result.status != AgentStatus.SUCCESS or "plan" not in plan_result.artifacts:
                await streaming_manager.publish_error(
                    TOPIC_AGENT, session_id,
                    plan_result.error or "Planning failed",
                    code="PLANNING_FAILED",
                )
                return

            plan = plan_result.artifacts["plan"]
            await streaming_manager.publish(StreamEvent(
                topic=f"{TOPIC_AGENT}:{session_id}",
                event_type="data",
                payload={
                    "kind": "plan",
                    "plan_id": plan.plan_id,
                    "summary": plan.summary,
                    "tasks": [t.to_dict() if hasattr(t, "to_dict") else _task_dict(t) for t in plan.tasks],
                },
            ))

            executor = DAGExecutor(
                tool_registry=registry, memory=memory, progress_cb=_cb,
            )
            report = await executor.execute(
                plan=plan,
                user_prompt=req.prompt,
                connection=req.connection or {},
                files=req.files,
                schema_context=schema,
            )
            await streaming_manager.publish_complete(
                TOPIC_AGENT, session_id, report.to_dict(),
            )
        except Exception as exc:  # pragma: no cover
            await streaming_manager.publish_error(
                TOPIC_AGENT, session_id, str(exc), code="AGENT_FAILED",
            )

    from shared.tasks import fire_and_forget
    fire_and_forget(_run(), name=f"agent-async-{session_id}")
    return AgentAsyncResponse(
        session_id=session_id,
        topic=f"{TOPIC_AGENT}:{session_id}",
    )


@router.get("/tools")
async def list_tools():
    """List all registered agent tools."""
    registry = _make_registry()
    tools = registry.list_tools()
    return {
        "tools": [
            {"name": t.name, "description": t.description, "category": t.category}
            for t in tools
        ]
    }


# ── Private helpers ───────────────────────────────────────────────────

def _task_dict(t: Any) -> Dict[str, Any]:
    tt = getattr(t, "task_type", "")
    return {
        "id": getattr(t, "id", "?"),
        "task_type": tt.value if hasattr(tt, "value") else str(tt),
        "description": getattr(t, "description", ""),
        "agent_name": getattr(t, "agent_name", ""),
        "depends_on": getattr(t, "depends_on", []),
    }
