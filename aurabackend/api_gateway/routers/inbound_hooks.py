"""
Inbound Hooks Router
=====================
CRUD for inbound hooks + the public trigger endpoint:

    POST /hooks/{slug}    fire a hook (auth via optional HMAC secret)
    GET  /hooks            list registered hooks
    POST /hooks            register a hook
    GET  /hooks/{id}       fetch one
    PATCH /hooks/{id}      update
    DELETE /hooks/{id}     unregister

When fired:
  - kind="pipeline": loads the saved Pipeline by id and calls
    /pipeline/execute/async equivalent in-process — returns {run_id, topic}.
  - kind="agent": kicks off an /agent/execute/async equivalent with the
    hook's prompt template; if pass_payload_as is set, the JSON body is
    merged into the agent's schema_context under that key.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from shared.inbound_hooks import inbound_hooks
from shared.logging_config import get_logger
from shared.streaming_manager import TOPIC_AGENT, TOPIC_PIPELINE, StreamEvent, streaming_manager

logger = get_logger("aura.api_gateway.inbound_hooks")

router = APIRouter(tags=["Inbound Hooks"])


# ── Request models ─────────────────────────────────────────────────

class HookCreateRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    kind: str = Field(..., description="'pipeline' or 'agent'")
    target: str = Field(..., description="pipeline_id or agent prompt")
    secret: Optional[str] = None
    description: str = ""
    pass_payload_as: Optional[str] = None


class HookUpdateRequest(BaseModel):
    slug: Optional[str] = None
    kind: Optional[str] = None
    target: Optional[str] = None
    secret: Optional[str] = None
    description: Optional[str] = None
    pass_payload_as: Optional[str] = None
    active: Optional[bool] = None


# ── CRUD ───────────────────────────────────────────────────────────

@router.get("/hooks")
async def list_hooks() -> Dict[str, Any]:
    return {"status": "success", "hooks": [h.to_dict() for h in inbound_hooks.list()]}


@router.post("/hooks")
async def create_hook(req: HookCreateRequest) -> Dict[str, Any]:
    try:
        hook = inbound_hooks.register(
            slug=req.slug,
            kind=req.kind,
            target=req.target,
            secret=req.secret,
            description=req.description,
            pass_payload_as=req.pass_payload_as,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "hook": hook.to_dict()}


@router.get("/hooks/{hook_id}")
async def get_hook(hook_id: str) -> Dict[str, Any]:
    h = inbound_hooks.get(hook_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"status": "success", "hook": h.to_dict()}


@router.patch("/hooks/{hook_id}")
async def update_hook(hook_id: str, req: HookUpdateRequest) -> Dict[str, Any]:
    try:
        h = inbound_hooks.update(hook_id, **req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not h:
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"status": "success", "hook": h.to_dict()}


@router.delete("/hooks/{hook_id}")
async def delete_hook(hook_id: str) -> Dict[str, Any]:
    if not inbound_hooks.delete(hook_id):
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"status": "success", "deleted": hook_id}


# ── Trigger endpoint ───────────────────────────────────────────────

def _verify_signature(secret: str, body: bytes, signature_header: Optional[str]) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    sig = signature_header
    if sig.startswith("sha256="):
        sig = sig[len("sha256="):]
    return hmac.compare_digest(expected, sig)


@router.post("/hooks/fire/{slug}")
async def fire_hook(slug: str, request: Request) -> Dict[str, Any]:
    """Public trigger endpoint — POST a JSON body to launch the hook."""
    hook = inbound_hooks.by_slug(slug)
    if not hook:
        raise HTTPException(status_code=404, detail="Hook not found")
    if not hook.active:
        raise HTTPException(status_code=403, detail="Hook is disabled")

    body = await request.body()
    if hook.secret:
        sig = request.headers.get("x-aura-signature") or request.headers.get("x-hub-signature-256")
        if not _verify_signature(hook.secret, body, sig):
            raise HTTPException(status_code=401, detail="Invalid or missing signature")

    try:
        payload = await request.json() if body else {}
    except Exception:
        payload = {"_raw": body.decode("utf-8", errors="replace")}

    inbound_hooks.record_fire(hook)

    # Announce on the streaming bus so audits / outbound webhooks see the trigger.
    try:
        await streaming_manager.publish(StreamEvent(
            topic=f"hooks:{hook.slug}",
            event_type="complete",
            payload={
                "hook_id": hook.id, "slug": hook.slug, "kind": hook.kind,
                "fire_count": hook.fire_count,
            },
        ))
    except Exception:
        pass

    if hook.kind == "pipeline":
        return await _fire_pipeline(hook.target, payload)
    elif hook.kind == "agent":
        return await _fire_agent(hook.target, payload, hook.pass_payload_as)
    else:
        raise HTTPException(status_code=500, detail=f"Unknown hook kind: {hook.kind}")


# ── Trigger helpers ────────────────────────────────────────────────

async def _fire_pipeline(pipeline_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from api_gateway.routers.pipelines import _pipeline_engine
    from pipeline.models import Pipeline as PipelineModel

    pipeline: Optional[PipelineModel] = _pipeline_engine.get(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")

    run_id = uuid.uuid4().hex
    preview_only = bool(payload.get("preview_only", False))

    async def _run() -> None:
        topic = f"{TOPIC_PIPELINE}:{run_id}"
        try:
            await streaming_manager.publish(StreamEvent(
                topic=topic, event_type="data",
                payload={"kind": "plan", "pipeline_id": pipeline.id,
                         "pipeline_name": pipeline.name,
                         "trigger": "inbound-hook"},
            ))

            async def _kafka_cb(consumed: int, lag: Optional[int]) -> None:
                await streaming_manager.publish(StreamEvent(
                    topic=topic, event_type="progress",
                    payload={"message": f"Kafka consumed {consumed} rows",
                             "stage": "source", "consumed": consumed, "lag": lag},
                ))

            run = await _pipeline_engine.execute(
                pipeline, preview_only=preview_only, source_progress_cb=_kafka_cb,
            )
            if run.status.value == "failed":
                await streaming_manager.publish_error(
                    TOPIC_PIPELINE, run_id, run.error or "Pipeline failed",
                    code="PIPELINE_FAILED",
                )
                return
            await streaming_manager.publish_complete(
                TOPIC_PIPELINE, run_id, run.model_dump(),
            )
        except Exception as exc:
            await streaming_manager.publish_error(
                TOPIC_PIPELINE, run_id, str(exc), code="PIPELINE_FAILED",
            )

    from shared.tasks import fire_and_forget
    fire_and_forget(_run(), name=f"hook-pipeline-{run_id}")
    return {
        "status": "success", "kind": "pipeline",
        "pipeline_id": pipeline.id, "run_id": run_id,
        "topic": f"{TOPIC_PIPELINE}:{run_id}",
    }


async def _fire_agent(
    prompt_template: str,
    payload: Dict[str, Any],
    pass_payload_as: Optional[str],
) -> Dict[str, Any]:
    from agents.base import AgentContext, AgentStatus
    from agents.executor import DAGExecutor
    from agents.memory import AgentMemory
    from agents.planner import PlannerAgent
    from agents.tool_registry import ToolRegistry
    from agents.tools import register_all_tools

    session_id = uuid.uuid4().hex
    schema_context: Dict[str, Any] = {}
    if pass_payload_as:
        schema_context[pass_payload_as] = payload

    registry = ToolRegistry()
    register_all_tools(registry)
    memory = AgentMemory()

    async def _run() -> None:
        try:
            async def _cb(message: str, agent: str, pct: float) -> None:
                await streaming_manager.publish_progress(
                    TOPIC_AGENT, session_id, message, float(pct or 0.0),
                    extra={"agent": agent},
                )

            planner = PlannerAgent()
            planner.set_progress_callback(_cb)
            ctx = AgentContext(
                user_prompt=prompt_template,
                task_description=prompt_template,
                session_id=session_id,
                files=[],
                connection={},
                schema_context=schema_context,
                metadata={"trigger": "inbound-hook"},
            )
            plan_result = await planner.execute(ctx)
            if plan_result.status != AgentStatus.SUCCESS or "plan" not in plan_result.artifacts:
                await streaming_manager.publish_error(
                    TOPIC_AGENT, session_id,
                    plan_result.error or "Planning failed", code="PLANNING_FAILED",
                )
                return

            plan = plan_result.artifacts["plan"]
            executor = DAGExecutor(tool_registry=registry, memory=memory, progress_cb=_cb)
            report = await executor.execute(
                plan=plan, user_prompt=prompt_template, connection={},
                files=[], schema_context=schema_context,
            )
            await streaming_manager.publish_complete(
                TOPIC_AGENT, session_id, report.to_dict(),
            )
        except Exception as exc:
            await streaming_manager.publish_error(
                TOPIC_AGENT, session_id, str(exc), code="AGENT_FAILED",
            )

    from shared.tasks import fire_and_forget
    fire_and_forget(_run(), name=f"hook-agent-{session_id}")
    return {
        "status": "success", "kind": "agent",
        "session_id": session_id, "topic": f"{TOPIC_AGENT}:{session_id}",
    }
