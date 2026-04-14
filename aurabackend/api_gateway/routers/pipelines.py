"""
Pipelines Router
=================
AI-driven pipeline management, semantic models, and UASR proxy endpoints.
"""

import asyncio
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from shared.logging_config import get_logger
from shared.streaming_manager import streaming_manager, StreamEvent, TOPIC_PIPELINE

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
async def pipeline_generate(req: PipelineGenerateRequest):
    """Convert a natural language prompt into a Pipeline definition."""
    logger.info("[Pipeline] Generate request: %s", req.prompt[:200])
    gen = _get_generator()

    schema_context = None
    available_files = None
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "uploads")
    skip = {".gitkeep", ".DS_Store"}
    data_exts = {".csv", ".parquet", ".json", ".xlsx", ".tsv"}
    if os.path.isdir(upload_dir):
        available_files = [f for f in sorted(os.listdir(upload_dir)) if f not in skip and os.path.splitext(f)[1].lower() in data_exts]

    if req.include_schema:
        schema_context = {}
        target_file = req.source_file
        if not target_file and available_files:
            target_file = available_files[0]
        if target_file:
            try:
                schema_context[target_file] = gen.get_file_schema(target_file)
            except Exception as e:
                logger.warning("[Pipeline] Schema read failed for %s: %s", target_file, e)

    try:
        pipeline = await gen.generate(prompt=req.prompt, available_files=available_files, schema_context=schema_context)
        if req.source_file and pipeline.source.type.value == "file":
            pipeline.source.file_name = req.source_file
        return {"status": "success", "pipeline": pipeline.model_dump()}
    except Exception as e:
        logger.error("[Pipeline] Generate failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


@router.post("/pipeline/execute")
async def pipeline_execute(req: PipelineExecuteRequest):
    """Execute a pipeline definition and return results."""
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid pipeline: {e}")
    try:
        run = await _pipeline_engine.execute(pipeline, preview_only=req.preview_only)
        return {"status": "success", "run": run.model_dump()}
    except Exception as e:
        logger.error("[Pipeline] Execute failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


@router.post("/pipeline/execute/async")
async def pipeline_execute_async(req: PipelineExecuteRequest):
    """Kick off pipeline execution in the background and publish live progress.

    Returns ``{run_id, topic}``. Subscribe to ``GET /stream/pipeline:{run_id}``
    for plan + stage progress + final run result.
    """
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid pipeline: {e}")

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

            run = await _pipeline_engine.execute(
                pipeline, preview_only=req.preview_only,
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
            await streaming_manager.publish_error(
                TOPIC_PIPELINE, run_id, str(exc), code="PIPELINE_FAILED",
            )

    asyncio.create_task(_run())
    return {"status": "success", "run_id": run_id, "topic": f"{TOPIC_PIPELINE}:{run_id}"}


@router.post("/pipeline/save")
async def pipeline_save(req: PipelineSaveRequest):
    """Save a pipeline definition for later use."""
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid pipeline: {e}")
    saved = _pipeline_engine.save(pipeline)
    return {"status": "success", "pipeline_id": saved.id, "name": saved.name}


@router.get("/pipeline/list")
async def pipeline_list():
    """List all saved pipelines."""
    pipelines = _pipeline_engine.list_all()
    return {
        "status": "success", "count": len(pipelines),
        "pipelines": [
            {"id": p.id, "name": p.name, "description": p.description, "source": p.source.label(), "steps": len(p.steps), "sink": p.sink.type.value, "status": p.status.value, "created_at": p.created_at, "tags": p.tags}
            for p in pipelines
        ],
    }


@router.get("/pipeline/{pipeline_id}")
async def pipeline_get(pipeline_id: str):
    """Get a saved pipeline by ID."""
    p = _pipeline_engine.get(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "pipeline": p.model_dump()}


@router.delete("/pipeline/{pipeline_id}")
async def pipeline_delete(pipeline_id: str):
    """Delete a saved pipeline."""
    deleted = _pipeline_engine.delete(pipeline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "deleted": pipeline_id}


@router.get("/pipeline/schema/{file_name}")
async def pipeline_file_schema(file_name: str):
    """Get column schema for a file."""
    gen = _get_generator()
    try:
        schema = gen.get_file_schema(file_name)
        return {"status": "success", "schema": schema}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/pipeline/download/{filename}")
async def pipeline_download(filename: str):
    """Download a pipeline output file."""
    from fastapi.responses import FileResponse
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "processed")
    file_path = os.path.join(output_dir, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(file_path, filename=filename)


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
async def auto_generate_model_from_file(file_id: str) -> Dict[str, Any]:
    """Auto-generate semantic model from dataset profile."""
    if semantic_builder is None or get_repository is None:
        return {"status": "error", "error": "Semantic builder or repository not available"}
    try:
        async for repo in get_repository():
            profile_record = await repo.get_dataset_profile(file_id)
            if profile_record is None:
                raise HTTPException(status_code=404, detail="Dataset profile not found")
            model_payload = semantic_builder.generate_model_from_profile(file_id=file_id, dataset_name=profile_record.dataset_name or f"dataset_{file_id[:8]}", profile=profile_record.profile)
            model = await repo.upsert_semantic_model(model_id=None, name=model_payload['name'], description=model_payload['description'], source=model_payload['source'], tags=model_payload['tags'], fields=model_payload['fields'])
            break
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/semantic/models")
async def upsert_semantic_model(payload: SemanticModelPayload) -> Dict[str, Any]:
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}
    try:
        async for repo in get_repository():
            model = await repo.upsert_semantic_model(model_id=payload.id, name=payload.name, description=payload.description, source=payload.source, tags=payload.tags, fields=[field.model_dump() for field in payload.fields])
            break
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/semantic/models")
async def list_semantic_models() -> Dict[str, Any]:
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}
    try:
        async for repo in get_repository():
            models = await repo.list_semantic_models()
            break
        return {"status": "success", "models": [_serialize_semantic_model(m) for m in models]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/semantic/models/{model_id}")
async def get_semantic_model(model_id: str) -> Dict[str, Any]:
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}
    try:
        async for repo in get_repository():
            model = await repo.get_semantic_model(model_id)
            break
        if model is None:
            raise HTTPException(status_code=404, detail="Semantic model not found")
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── UASR Self-Healing proxy routes ───────────────────────────────────

_UASR_URL = os.getenv("AURA_UASR_URL", "http://localhost:8009")


@router.post("/uasr/ingest")
async def uasr_ingest(req: Dict[str, Any]):
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{_UASR_URL}/uasr/ingest", json=req)
        return resp.json()


@router.post("/uasr/baseline")
async def uasr_baseline(req: Dict[str, Any]):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{_UASR_URL}/uasr/baseline", json=req)
        return resp.json()


@router.get("/uasr/metrics")
async def uasr_metrics():
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{_UASR_URL}/uasr/metrics")
        return resp.json()


@router.get("/uasr/drift/status")
async def uasr_drift_status(source_id: str = None):
    params = {}
    if source_id:
        params["source_id"] = source_id
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{_UASR_URL}/uasr/drift/status", params=params)
        return resp.json()
