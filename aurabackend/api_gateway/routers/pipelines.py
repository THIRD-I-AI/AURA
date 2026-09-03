"""
Pipelines Router
=================
AI-driven pipeline management, semantic models, and UASR proxy endpoints.
"""

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api_gateway.persistence import (
    delete_pipeline,
    get_pipeline,
    list_pipelines,
    save_pipeline,
)
from shared.error_handler import sanitize_error
from shared.exceptions import ServiceUnavailableError
from shared.logging_config import get_logger
from shared.storage import get_storage_backend
from shared.streaming_manager import TOPIC_PIPELINE, StreamEvent, streaming_manager

from .workspaces import _request_tenant, current_workspace_id

logger = get_logger("aura.api_gateway.pipelines")

router = APIRouter(tags=["Pipelines"])


# ── Pipeline engine singletons ───────────────────────────────────────

from pipeline.engine import PipelineEngine
from pipeline.generator import PipelineGenerator
from pipeline.models import Pipeline as PipelineModel

_pipeline_engine = PipelineEngine()
_pipeline_generator: Optional[PipelineGenerator] = None


def _get_generator() -> PipelineGenerator:
    global _pipeline_generator
    if _pipeline_generator is None:
        _pipeline_generator = PipelineGenerator()
    return _pipeline_generator


# ── Metadata / Semantic imports ──────────────────────────────────────

try:
    from metadata_store.repository import get_repository
except ImportError:
    get_repository = None

try:
    from semantic_builder import semantic_builder
except ImportError:
    semantic_builder = None


# ── Models ───────────────────────────────────────────────────────────

class PipelineGenerateRequest(BaseModel):
    prompt: str
    source_file: Optional[str] = None
    include_schema: bool = True


class PipelineExecuteRequest(BaseModel):
    pipeline: Dict[str, Any]
    preview_only: bool = False


class PipelineSaveRequest(BaseModel):
    pipeline: Dict[str, Any]


class SemanticFieldPayload(BaseModel):
    id: Optional[str] = None
    name: str
    field_type: str = Field(default="dimension", description="dimension | measure")
    data_type: Optional[str] = None
    expression: Optional[str] = None
    description: Optional[str] = None
    aggregation: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticModelPayload(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    fields: List[SemanticFieldPayload] = Field(default_factory=list)


# ── Pipeline endpoints ───────────────────────────────────────────────

@router.post("/pipeline/generate")
async def pipeline_generate(req: PipelineGenerateRequest, request: Request):
    """Convert a natural language prompt into a Pipeline definition."""
    logger.info("[Pipeline] Generate request: %s", req.prompt[:200])
    gen = _get_generator()

    schema_context = None
    tenant = _request_tenant(request)
    # BUG-035: list the caller's uploaded files through the active
    # StorageBackend rather than scanning a local directory, so this works
    # under both AURA_STORAGE_BACKEND=local and =s3. Tradeoff: the backend
    # only lists the extensions it already indexes for reading
    # (.csv/.parquet/.json) — .xlsx/.tsv are no longer discovered here.
    available_files = [obj.name for obj in get_storage_backend().list(tenant)]

    if req.include_schema:
        schema_context = {}
        target_file = req.source_file
        if not target_file and available_files:
            target_file = available_files[0]
        if target_file:
            try:
                schema_context[target_file] = gen.get_file_schema(target_file, tenant)
            except Exception as e:
                logger.warning("[Pipeline] Schema read failed for %s: %s", target_file, e)

    try:
        pipeline = await gen.generate(
            prompt=req.prompt, available_files=available_files, schema_context=schema_context, tenant=tenant,
        )
        if req.source_file and pipeline.source.type.value == "file":
            pipeline.source.file_name = req.source_file
        return {"status": "success", "pipeline": pipeline.model_dump()}
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="pipeline generate")}


@router.post("/pipeline/execute")
async def pipeline_execute(req: PipelineExecuteRequest, request: Request):
    """Execute a pipeline definition and return results."""
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        # Pipeline-model validation errors are user-facing (they tell
        # the caller what's wrong with their payload), but we still
        # don't echo the raw exception text — log + generic message.
        logger.warning("[Pipeline] Invalid pipeline payload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid pipeline payload")
    try:
        run = await _pipeline_engine.execute(
            pipeline, preview_only=req.preview_only, tenant=_request_tenant(request),
        )
        return {"status": "success", "run": run.model_dump()}
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="pipeline execute")}


@router.post("/pipeline/execute/async")
async def pipeline_execute_async(req: PipelineExecuteRequest, request: Request):
    """Kick off pipeline execution in the background and publish live progress.

    Returns ``{run_id, topic}``. Subscribe to ``GET /stream/pipeline:{run_id}``
    for plan + stage progress + final run result.
    """
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        logger.warning("[Pipeline] Invalid pipeline payload on async execute: %s", e)
        raise HTTPException(status_code=400, detail="Invalid pipeline payload")

    # Resolved eagerly, in the request handler — the background task
    # below outlives the request and must not depend on request.state
    # still being valid by the time it runs.
    tenant = _request_tenant(request)
    run_id = uuid.uuid4().hex

    async def _run() -> None:
        topic = f"{TOPIC_PIPELINE}:{run_id}"
        try:
            steps_meta = [
                {"id": getattr(s, "id", str(i)), "name": getattr(s, "name", getattr(s, "type", f"step_{i}")),
                 "type": getattr(getattr(s, "type", ""), "value", str(getattr(s, "type", "")))}
                for i, s in enumerate(pipeline.steps or [])
            ]
            await streaming_manager.publish(StreamEvent(
                topic=topic, event_type="data",
                payload={
                    "kind": "plan",
                    "pipeline_id": pipeline.id,
                    "pipeline_name": pipeline.name,
                    "source": pipeline.source.label() if hasattr(pipeline.source, "label") else str(pipeline.source),
                    "sink": getattr(getattr(pipeline.sink, "type", ""), "value", ""),
                    "steps": steps_meta,
                },
            ))

            await streaming_manager.publish_progress(
                TOPIC_PIPELINE, run_id, "Loading source", 0.10,
                extra={"stage": "source"},
            )
            await streaming_manager.publish_progress(
                TOPIC_PIPELINE, run_id, "Building transform SQL", 0.25,
                extra={"stage": "build_sql"},
            )

            # Publish a "running" event per declared step (coarse — engine
            # does not expose mid-run hooks, so these mark intent).
            n = max(1, len(steps_meta))
            for i, s in enumerate(steps_meta):
                pct = 0.30 + 0.50 * ((i + 1) / (n + 1))
                await streaming_manager.publish_progress(
                    TOPIC_PIPELINE, run_id,
                    f"Running step: {s['name']}", pct,
                    extra={"stage": "transform", "step_id": s["id"], "step_index": i},
                )

            async def _kafka_cb(consumed: int, lag: Optional[int]) -> None:
                await streaming_manager.publish(StreamEvent(
                    topic=f"{TOPIC_PIPELINE}:{run_id}",
                    event_type="progress",
                    payload={
                        "message": f"Kafka consumed {consumed} rows"
                                   + (f" (lag ~{lag})" if lag is not None else ""),
                        "progress": 0.15,
                        "stage": "source",
                        "source_kind": "kafka",
                        "consumed": consumed,
                        "lag": lag,
                    },
                ))

            run = await _pipeline_engine.execute(
                pipeline, preview_only=req.preview_only,
                source_progress_cb=_kafka_cb,
                tenant=tenant,
            )

            await streaming_manager.publish_progress(
                TOPIC_PIPELINE, run_id,
                "Sink complete" if not req.preview_only else "Preview ready",
                0.95, extra={"stage": "sink"},
            )

            if run.status.value == "failed":
                await streaming_manager.publish_error(
                    TOPIC_PIPELINE, run_id,
                    run.error or "Pipeline failed", code="PIPELINE_FAILED",
                )
                return

            await streaming_manager.publish_complete(
                TOPIC_PIPELINE, run_id, run.model_dump(),
            )
        except Exception as exc:
            # Sec-2 #27: don't echo raw exception text out over the
            # SSE stream — log full detail server-side and send a
            # generic failure code to the client.
            logger.error("[Pipeline] Async run %s failed: %s", run_id, exc, exc_info=True)
            await streaming_manager.publish_error(
                TOPIC_PIPELINE, run_id, "Pipeline failed", code="PIPELINE_FAILED",
            )

    from shared.tasks import fire_and_forget
    fire_and_forget(_run(), name=f"pipeline-async-{run_id}")
    return {"status": "success", "run_id": run_id, "topic": f"{TOPIC_PIPELINE}:{run_id}"}


@router.post("/pipeline/save")
async def pipeline_save(req: PipelineSaveRequest, request: Request):
    """Save a pipeline definition for later use (tenant-scoped, durable)."""
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        logger.warning("[Pipeline] Invalid pipeline on save: %s", e)
        raise HTTPException(status_code=400, detail="Invalid pipeline payload")
    saved = await save_pipeline({
        "id": pipeline.id,
        "workspace_id": current_workspace_id(request),
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
    return {"status": "success", "pipeline_id": saved["id"], "name": saved["name"]}


@router.get("/pipeline/list")
async def pipeline_list(request: Request):
    """List the caller-tenant's saved pipelines."""
    pipelines = await list_pipelines(current_workspace_id(request))
    return {"status": "success", "count": len(pipelines), "pipelines": pipelines}


@router.get("/pipeline/{pipeline_id}")
async def pipeline_get(pipeline_id: str, request: Request):
    """Get one of the caller-tenant's saved pipelines by ID."""
    definition = await get_pipeline(pipeline_id, workspace_id=current_workspace_id(request))
    if not definition:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "pipeline": definition}


@router.delete("/pipeline/{pipeline_id}")
async def pipeline_delete(pipeline_id: str, request: Request):
    """Delete one of the caller-tenant's saved pipelines."""
    deleted = await delete_pipeline(pipeline_id, current_workspace_id(request))
    if not deleted:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "deleted": pipeline_id}


@router.get("/pipeline/schema/{file_name}")
async def pipeline_file_schema(file_name: str, request: Request):
    """Get column schema for a file."""
    gen = _get_generator()
    try:
        schema = gen.get_file_schema(file_name, _request_tenant(request))
        return {"status": "success", "schema": schema}
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="pipeline file schema")}


@router.get("/pipeline/download/{filename}")
async def pipeline_download(filename: str):
    """Download a pipeline output file."""
    from fastapi.responses import FileResponse
    output_dir = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "data" / "processed"
    # Sec-2 #40-#41: inline sanitizer (realpath + startswith) at the
    # FileResponse sink — the canonical CodeQL py/path-injection
    # sanitizer pattern that the standard model recognises directly.
    if (not filename) or os.path.isabs(filename) or any(p == ".." for p in Path(filename).parts):
        raise HTTPException(status_code=400, detail="Invalid filename")
    output_dir_real = os.path.realpath(str(output_dir)) + os.sep
    file_path_str = os.path.realpath(os.path.join(output_dir_real, filename))
    if not file_path_str.startswith(output_dir_real):
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = Path(file_path_str)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(file_path_str, filename=file_path.name)


# ── Semantic Model endpoints ─────────────────────────────────────────

def _serialize_semantic_model(model: Any) -> Dict[str, Any]:
    return {
        "id": model.id, "name": model.name, "description": model.description,
        "source": model.source, "tags": model.tags,
        "created_at": model.created_at, "updated_at": model.updated_at,
        "fields": [
            {"id": field.id, "name": field.name, "field_type": field.field_type, "data_type": field.data_type, "expression": field.expression, "description": field.description, "aggregation": field.aggregation, "metadata": field.metadata, "created_at": field.created_at, "updated_at": field.updated_at}
            for field in getattr(model, "fields", [])
        ],
    }


@router.post("/semantic/models/from-file/{file_id}")
async def auto_generate_model_from_file(file_id: str, request: Request) -> Dict[str, Any]:
    """Auto-generate semantic model from dataset profile.

    The generated model is stamped with the caller's workspace so it is only
    ever readable by them.

    Note the remaining gap this route inherits: get_dataset_profile is still
    unscoped, because dataset_profiles has no tenant column yet (the
    /files/{id}/profile route contains the equivalent read by checking file
    ownership instead). Giving that table its own column is tracked with the
    rest of the metadata_store schema.
    """
    if semantic_builder is None or get_repository is None:
        return {"status": "error", "error": "Semantic builder or repository not available"}
    try:
        async for repo in get_repository():
            profile_record = await repo.get_dataset_profile(file_id)
            if profile_record is None:
                raise HTTPException(status_code=404, detail="Dataset profile not found")
            model_payload = semantic_builder.generate_model_from_profile(file_id=file_id, dataset_name=profile_record.dataset_name or f"dataset_{file_id[:8]}", profile=profile_record.profile)
            model = await repo.upsert_semantic_model(model_id=None, name=model_payload['name'], description=model_payload['description'], source=model_payload['source'], tags=model_payload['tags'], fields=model_payload['fields'], workspace_id=current_workspace_id(request))
            break
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="semantic model auto-generate")}


@router.post("/semantic/models")
async def upsert_semantic_model(payload: SemanticModelPayload, request: Request) -> Dict[str, Any]:
    """Create or update a semantic model within the caller's workspace.

    The workspace also scopes the UPDATE lookup in the repository, not just the
    INSERT. Without that, posting another tenant's model id would load THEIR
    row and overwrite it — a cross-tenant write, which is worse than the read
    this change started from.
    """
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}
    try:
        async for repo in get_repository():
            model = await repo.upsert_semantic_model(model_id=payload.id, name=payload.name, description=payload.description, source=payload.source, tags=payload.tags, fields=[field.model_dump() for field in payload.fields], workspace_id=current_workspace_id(request))
            break
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="semantic model upsert")}


@router.get("/semantic/models")
async def list_semantic_models(request: Request) -> Dict[str, Any]:
    """List the CALLER'S semantic models.

    This returned every tenant's models: the repository ran a bare
    select(SemanticModel) and this route took no Request, so it had nothing to
    scope by. A semantic model carries field names, expressions and
    descriptions derived from the owner's data, so the leak was modelled
    business logic, not merely row counts.

    current_workspace_id is the same isolation key the pipeline routes above
    use — derived from the verified JWT, never a client header.
    """
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}
    try:
        async for repo in get_repository():
            models = await repo.list_semantic_models(current_workspace_id(request))
            break
        return {"status": "success", "models": [_serialize_semantic_model(m) for m in models]}
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="semantic model list")}


@router.get("/semantic/models/{model_id}")
async def get_semantic_model(model_id: str, request: Request) -> Dict[str, Any]:
    """Fetch one model, scoped to the caller's workspace.

    Another tenant's id now misses the query and falls through to the existing
    404 below — the same not-found answer as a genuinely unknown id, so this
    never confirms that the id exists under a different tenant.
    """
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}
    try:
        async for repo in get_repository():
            model = await repo.get_semantic_model(model_id, current_workspace_id(request))
            break
        if model is None:
            raise HTTPException(status_code=404, detail="Semantic model not found")
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="semantic model get")}


# ── UASR Self-Healing proxy routes ───────────────────────────────────

_UASR_URL = os.getenv("AURA_UASR_URL", "http://localhost:8009")


async def _uasr(
    method: str,
    path: str,
    timeout: float,
    request: Optional[Request] = None,
    **kwargs: Any,
) -> Any:
    """Proxy a call to the UASR service, failing honestly when it is absent.

    UASR is a separate service and not every deployment profile runs it — the
    single-instance profile runs the gateway alone. When it is missing, httpx
    raises ConnectError and the generic handler renders that as
    500 "An unexpected error occurred". That is misleading in both
    directions: it reads as a crash inside AURA, and it gives an operator
    nothing to act on. Verified on the live deployment, where /uasr/metrics,
    /uasr/drift/status and /uasr/recovery/pending all returned 500 for this
    reason alone.

    503 is the accurate answer for a dependency that is unreachable rather
    than broken. RequestError (not just ConnectError) is caught because a
    timeout or DNS failure means the same thing to a caller. The upstream URL
    is logged, never returned — it is internal topology.
    """
    # UASR enforces the same bearer auth the gateway does, so the caller's
    # token has to travel with the proxied request. Without it every call came
    # back "Bearer token required" — and, because the old code returned
    # resp.json() and dropped resp.status_code, that 401 reached the browser
    # as HTTP 200 with an error body: a failure wearing a success status, the
    # same shape as a connector reporting healthy while pointing at nothing.
    headers = {}
    if request is not None:
        auth = request.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{_UASR_URL}{path}", headers=headers, **kwargs)
    except httpx.RequestError as exc:
        logger.warning("UASR unreachable at %s%s: %s", _UASR_URL, path, exc)
        raise ServiceUnavailableError("UASR self-healing service") from exc

    # Preserve the upstream status. A proxy that rewrites every outcome to 200
    # makes the caller parse bodies to discover failure.
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": "UPSTREAM_NOT_JSON", "message": resp.text[:500]}
    return JSONResponse(status_code=resp.status_code, content=payload)


@router.post("/uasr/ingest")
async def uasr_ingest(req: Dict[str, Any], request: Request):
    return await _uasr("POST", "/uasr/ingest", 60, request, json=req)


@router.post("/uasr/baseline")
async def uasr_baseline(req: Dict[str, Any], request: Request):
    return await _uasr("POST", "/uasr/baseline", 30, request, json=req)


@router.post("/uasr/schema-intent")
async def uasr_schema_intent(req: Dict[str, Any], request: Request):
    return await _uasr("POST", "/uasr/schema-intent", 30, request, json=req)


@router.post("/uasr/heal")
async def uasr_heal(req: Dict[str, Any], request: Request):
    """Proxy to UASR's healed-rows endpoint.

    Found unreachable through the public API during live staging
    verification (2026-08-31, docs/superpowers/specs/2026-08-31-uasr-live-
    validation-and-benchmark.md): only ingest/baseline/metrics/drift-status/
    recovery-pending/approve/reject were proxied. /uasr/heal is the endpoint
    that actually returns healed rows -- a pipeline that can only reach
    /uasr/ingest gets a verdict and nothing more.
    """
    return await _uasr("POST", "/uasr/heal", 60, request, json=req)


@router.get("/uasr/metrics")
async def uasr_metrics(request: Request):
    return await _uasr("GET", "/uasr/metrics", 15, request)


@router.get("/uasr/deployment")
async def uasr_deployment(request: Request):
    return await _uasr("GET", "/uasr/deployment", 15, request)


@router.get("/uasr/correlation")
async def uasr_correlation(request: Request, window_seconds: float = None, min_sources: int = None):
    # UASR's /uasr/correlation endpoint ships in the cross-source-correlation
    # PR (candidate #5); until that merges, UASR answers 404 for this path
    # and the proxy honestly forwards that -- not a crash, just not live yet.
    params: Dict[str, Any] = {}
    if window_seconds is not None:
        params["window_seconds"] = window_seconds
    if min_sources is not None:
        params["min_sources"] = min_sources
    return await _uasr("GET", "/uasr/correlation", 15, request, params=params)


@router.get("/uasr/drift/status")
async def uasr_drift_status(request: Request, source_id: str = None):
    params = {"source_id": source_id} if source_id else {}
    return await _uasr("GET", "/uasr/drift/status", 15, request, params=params)


@router.get("/uasr/drift/{drift_event_id}/recovery")
async def uasr_recoveries_for_event(drift_event_id: str, request: Request):
    return await _uasr("GET", f"/uasr/drift/{drift_event_id}/recovery", 15, request)


@router.get("/uasr/metrics/history")
async def uasr_metrics_history(request: Request, limit: int = 50):
    return await _uasr("GET", "/uasr/metrics/history", 15, request, params={"limit": limit})


@router.get("/uasr/metrics/alerts")
async def uasr_metrics_alerts(request: Request, hu_floor: float = 0.3, resolution_floor: float = 0.5):
    params = {"hu_floor": hu_floor, "resolution_floor": resolution_floor}
    return await _uasr("GET", "/uasr/metrics/alerts", 15, request, params=params)


@router.post("/uasr/gate/check")
async def uasr_gate_check(req: Dict[str, Any], request: Request):
    return await _uasr("POST", "/uasr/gate/check", 15, request, json=req)


@router.post("/uasr/rollback")
async def uasr_rollback(req: Dict[str, Any], request: Request):
    return await _uasr("POST", "/uasr/rollback", 15, request, json=req)


@router.get("/uasr/shims/{source_id}")
async def uasr_list_shims(source_id: str, request: Request):
    return await _uasr("GET", f"/uasr/shims/{source_id}", 15, request)


@router.get("/uasr/references/{source_id}")
async def uasr_list_references(source_id: str, request: Request):
    return await _uasr("GET", f"/uasr/references/{source_id}", 15, request)


@router.get("/uasr/sources")
async def uasr_list_sources(request: Request):
    return await _uasr("GET", "/uasr/sources", 15, request)


# ── S41: supervised self-healing approval queue (proxied to UASR) ────

@router.get("/uasr/recovery/pending")
async def uasr_pending_approvals(request: Request, limit: int = 50):
    return await _uasr("GET", "/uasr/recovery/pending", 15, request, params={"limit": limit})


@router.post("/uasr/recovery/{recovery_id}/approve")
async def uasr_approve_recovery(recovery_id: str, req: Dict[str, Any], request: Request):
    return await _uasr("POST", f"/uasr/recovery/{recovery_id}/approve", 30, request, json=req)


@router.post("/uasr/recovery/{recovery_id}/reject")
async def uasr_reject_recovery(recovery_id: str, req: Dict[str, Any], request: Request):
    return await _uasr("POST", f"/uasr/recovery/{recovery_id}/reject", 30, request, json=req)


@router.get("/uasr/recovery/{recovery_id}")
async def uasr_recovery_detail(recovery_id: str, request: Request):
    """Declared after /uasr/recovery/pending and the /approve, /reject
    sub-paths on purpose -- FastAPI matches route declaration order, and a
    parameterized path declared first would swallow the literal ones
    (backend.md). Without this route, a failed recovery's diagnosis/error
    detail was undiagnosable through the public API -- confirmed live
    2026-08-31 when a genuine schema-drift auto-heal failed and there was
    no way to see why without box/log access.
    """
    return await _uasr("GET", f"/uasr/recovery/{recovery_id}", 15, request)


# ── Causal Discovery proxy routes ────────────────────────────────────
# causal_service is a separately-deployed microservice (DoWhy-GCM root-cause
# attribution, falls back to partial-correlation when dowhy isn't installed
# there) -- see aurabackend/causal_service/. Proxied the same way as UASR
# above, not mounted in-process: unlike counterfactual_service, it has no
# lifespan/signing coupling to the gateway process and its dowhy import is
# heavy enough (~150MB, see requirements-causal.txt) that isolating it in its
# own container keeps that weight off the gateway's memory budget.

_CAUSAL_URL = os.getenv("AURA_CAUSAL_URL", "http://localhost:8010")


async def _causal(
    method: str,
    path: str,
    timeout: float,
    request: Optional[Request] = None,
    **kwargs: Any,
) -> Any:
    """Proxy a call to causal_service, failing honestly when it is absent.

    Same reasoning as `_uasr` above: not every deployment profile runs this
    service, so an unreachable upstream must read as 503, not a generic 500
    that looks like a crash inside the gateway itself.
    """
    headers = {}
    if request is not None:
        auth = request.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{_CAUSAL_URL}{path}", headers=headers, **kwargs)
    except httpx.RequestError as exc:
        logger.warning("causal_service unreachable at %s%s: %s", _CAUSAL_URL, path, exc)
        raise ServiceUnavailableError("Causal discovery service") from exc

    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": "UPSTREAM_NOT_JSON", "message": resp.text[:500]}
    return JSONResponse(status_code=resp.status_code, content=payload)


@router.post("/causal/discover")
async def causal_discover(req: Dict[str, Any], request: Request):
    return await _causal("POST", "/causal/discover", 60, request, json=req)


@router.get("/causal/info")
async def causal_info(request: Request):
    return await _causal("GET", "/causal/info", 15, request)


# ── Scheduler service proxy routes ───────────────────────────────────
# scheduler_service (S20a/S20b/S20.2: Postgres LISTEN/NOTIFY wake,
# pg_advisory_lock leader election) had no gateway route at all -- only
# /system/health's port-8004 probe referenced it. Not part of the
# aws-free-tier single-box profile (see deploy/aws-free-tier/README.md);
# these routes matter for anyone running the full docker-compose stack.

_SCHEDULER_URL = os.getenv("AURA_SCHEDULER_URL", "http://localhost:8004")


async def _scheduler(
    method: str,
    path: str,
    timeout: float,
    request: Optional[Request] = None,
    **kwargs: Any,
) -> Any:
    """Proxy a call to the scheduler service, failing honestly when it is absent.

    Same shape as `_uasr` above: 503 for an unreachable dependency (not a
    500 that reads as a crash inside AURA), Authorization header forwarded,
    and upstream status code preserved rather than collapsed to 200.
    """
    headers = {}
    if request is not None:
        auth = request.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{_SCHEDULER_URL}{path}", headers=headers, **kwargs)
    except httpx.RequestError as exc:
        logger.warning("Scheduler service unreachable at %s%s: %s", _SCHEDULER_URL, path, exc)
        raise ServiceUnavailableError("Scheduler service") from exc

    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": "UPSTREAM_NOT_JSON", "message": resp.text[:500]}
    return JSONResponse(status_code=resp.status_code, content=payload)


@router.post("/scheduler/jobs")
async def scheduler_create_job(req: Dict[str, Any], request: Request):
    return await _scheduler("POST", "/jobs", 30, request, json=req)


@router.get("/scheduler/jobs")
async def scheduler_list_jobs(request: Request, is_active: Optional[bool] = None):
    params = {"is_active": is_active} if is_active is not None else {}
    return await _scheduler("GET", "/jobs", 15, request, params=params)


@router.get("/scheduler/jobs/{job_id}")
async def scheduler_get_job(job_id: str, request: Request):
    return await _scheduler("GET", f"/jobs/{job_id}", 15, request)


@router.put("/scheduler/jobs/{job_id}")
async def scheduler_update_job(job_id: str, req: Dict[str, Any], request: Request):
    return await _scheduler("PUT", f"/jobs/{job_id}", 30, request, json=req)


@router.delete("/scheduler/jobs/{job_id}")
async def scheduler_delete_job(job_id: str, request: Request):
    return await _scheduler("DELETE", f"/jobs/{job_id}", 15, request)


@router.post("/scheduler/jobs/{job_id}/pause")
async def scheduler_pause_job(job_id: str, request: Request):
    return await _scheduler("POST", f"/jobs/{job_id}/pause", 15, request)


@router.post("/scheduler/jobs/{job_id}/resume")
async def scheduler_resume_job(job_id: str, request: Request):
    return await _scheduler("POST", f"/jobs/{job_id}/resume", 15, request)


@router.post("/scheduler/jobs/{job_id}/execute")
async def scheduler_execute_job(job_id: str, request: Request):
    return await _scheduler("POST", f"/jobs/{job_id}/execute", 300, request)


@router.post("/scheduler/jobs/{job_id}/run")
async def scheduler_trigger_job_run(job_id: str, request: Request):
    return await _scheduler("POST", f"/jobs/{job_id}/run", 15, request)


@router.get("/scheduler/executions")
async def scheduler_list_executions(
    request: Request,
    job_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
):
    params: Dict[str, Any] = {"limit": limit}
    if job_id is not None:
        params["job_id"] = job_id
    if status is not None:
        # scheduler_service's own query param is `status_filter` (BUG-023a:
        # `status` there shadowed fastapi's `status` module). The gateway's
        # public param name stays `status` for API compatibility.
        params["status_filter"] = status
    return await _scheduler("GET", "/executions", 15, request, params=params)


@router.get("/scheduler/executions/{execution_id}")
async def scheduler_get_execution(execution_id: str, request: Request):
    return await _scheduler("GET", f"/executions/{execution_id}", 15, request)


@router.get("/scheduler/executions/{execution_id}/logs")
async def scheduler_get_execution_logs(execution_id: str, request: Request, level: Optional[str] = None):
    params = {"level": level} if level is not None else {}
    return await _scheduler("GET", f"/executions/{execution_id}/logs", 15, request, params=params)


@router.post("/scheduler/admin/cleanup")
async def scheduler_cleanup_old_executions(request: Request, retention_days: int = 30):
    return await _scheduler(
        "POST", "/admin/cleanup", 30, request, params={"retention_days": retention_days},
    )
