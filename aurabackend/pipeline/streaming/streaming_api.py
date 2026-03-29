"""
Streaming Pipeline API Routes
================================
FastAPI router for streaming pipeline management:
  - CRUD operations for streaming pipeline definitions
  - Start / stop / pause / resume lifecycle
  - SSE endpoint for real-time metrics & window results
  - Pipeline metrics polling
  - Template gallery
"""
from __future__ import annotations

import asyncio
import uuid
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pipeline.streaming.models import (
    StreamPipeline,
    StreamPipelineStatus,
    StreamMetrics,
    StreamSource,
    StreamSourceType,
    StreamSink,
    StreamSinkType,
    WindowConfig,
    WindowType,
    LateDataPolicy,
    StreamTransform,
    TransformType,
)
from pipeline.streaming.streaming_engine import StreamingEngine

logger = logging.getLogger("aura.streaming.api")

router = APIRouter(prefix="/streaming", tags=["Streaming Pipelines"])

# ────────────────────────────────────────────────────────────────────
# In-memory stores (thread-safe via GIL + async single-thread)
# ────────────────────────────────────────────────────────────────────

_pipelines: Dict[str, StreamPipeline] = {}
_engines: Dict[str, StreamingEngine] = {}


# ────────────────────────────────────────────────────────────────────
# Request / Response models
# ────────────────────────────────────────────────────────────────────

class CreateStreamPipelineRequest(BaseModel):
    name: str
    description: str = ""
    source: StreamSource
    event_time_field: str = "timestamp"
    watermark_delay_seconds: int = 10
    window: WindowConfig = Field(default_factory=WindowConfig)
    transforms: List[StreamTransform] = Field(default_factory=list)
    sinks: List[StreamSink] = Field(default_factory=list)
    checkpoint_interval_seconds: int = 30
    tags: List[str] = Field(default_factory=list)


class UpdateStreamPipelineRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source: Optional[StreamSource] = None
    event_time_field: Optional[str] = None
    watermark_delay_seconds: Optional[int] = None
    window: Optional[WindowConfig] = None
    transforms: Optional[List[StreamTransform]] = None
    sinks: Optional[List[StreamSink]] = None
    checkpoint_interval_seconds: Optional[int] = None
    tags: Optional[List[str]] = None


# ────────────────────────────────────────────────────────────────────
# CRUD
# ────────────────────────────────────────────────────────────────────

@router.get("/pipelines", summary="List all streaming pipelines")
async def list_pipelines():
    pipelines = []
    for pid, pipe in _pipelines.items():
        entry = pipe.model_dump()
        if pid in _engines:
            entry["metrics"] = _engines[pid].metrics.model_dump()
        pipelines.append(entry)
    return {"pipelines": pipelines, "total": len(pipelines)}


@router.get("/pipelines/{pipeline_id}", summary="Get pipeline details + metrics")
async def get_pipeline(pipeline_id: str):
    pipe = _pipelines.get(pipeline_id)
    if not pipe:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    result = pipe.model_dump()
    engine = _engines.get(pipeline_id)
    if engine:
        result["metrics"] = engine.metrics.model_dump()
    return result


@router.post("/pipelines", summary="Create a new streaming pipeline", status_code=201)
async def create_pipeline(req: CreateStreamPipelineRequest):
    # Ensure at least one SSE sink for frontend connectivity
    has_sse = any(s.type == StreamSinkType.SSE for s in req.sinks)
    sinks = list(req.sinks)
    if not has_sse:
        sinks.append(StreamSink(type=StreamSinkType.SSE, config={}))

    pipe = StreamPipeline(
        name=req.name,
        description=req.description,
        source=req.source,
        event_time_field=req.event_time_field,
        watermark_delay_seconds=req.watermark_delay_seconds,
        window=req.window,
        transforms=req.transforms,
        sinks=sinks,
        checkpoint_interval_seconds=req.checkpoint_interval_seconds,
        tags=req.tags,
    )
    _pipelines[pipe.id] = pipe
    logger.info("Created streaming pipeline: %s (%s)", pipe.name, pipe.id)
    return pipe.model_dump()


@router.put("/pipelines/{pipeline_id}", summary="Update a pipeline (must be stopped/draft)")
async def update_pipeline(pipeline_id: str, req: UpdateStreamPipelineRequest):
    pipe = _pipelines.get(pipeline_id)
    if not pipe:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pipe.status not in (StreamPipelineStatus.DRAFT, StreamPipelineStatus.STOPPED, StreamPipelineStatus.FAILED):
        raise HTTPException(status_code=409, detail="Pipeline must be stopped to edit")

    for field, value in req.model_dump(exclude_none=True).items():
        setattr(pipe, field, value)
    pipe.updated_at = datetime.utcnow().isoformat()
    return pipe.model_dump()


@router.delete("/pipelines/{pipeline_id}", summary="Delete a pipeline (must be stopped)")
async def delete_pipeline(pipeline_id: str):
    pipe = _pipelines.get(pipeline_id)
    if not pipe:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pipe.status == StreamPipelineStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Stop the pipeline before deleting")
    _pipelines.pop(pipeline_id, None)
    _engines.pop(pipeline_id, None)
    return {"deleted": pipeline_id}


# ────────────────────────────────────────────────────────────────────
# Lifecycle
# ────────────────────────────────────────────────────────────────────

@router.post("/pipelines/{pipeline_id}/start", summary="Start the pipeline")
async def start_pipeline(pipeline_id: str):
    pipe = _pipelines.get(pipeline_id)
    if not pipe:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pipe.status == StreamPipelineStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Already running")

    engine = StreamingEngine(pipe)
    _engines[pipeline_id] = engine
    await engine.start()
    return {"status": pipe.status.value, "pipeline_id": pipeline_id}


@router.post("/pipelines/{pipeline_id}/stop", summary="Stop the pipeline")
async def stop_pipeline(pipeline_id: str):
    engine = _engines.get(pipeline_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No running engine for this pipeline")
    await engine.stop()
    return {"status": engine.pipeline.status.value, "pipeline_id": pipeline_id}


@router.post("/pipelines/{pipeline_id}/pause", summary="Pause the pipeline")
async def pause_pipeline(pipeline_id: str):
    engine = _engines.get(pipeline_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No running engine for this pipeline")
    await engine.pause()
    return {"status": engine.pipeline.status.value, "pipeline_id": pipeline_id}


@router.post("/pipelines/{pipeline_id}/resume", summary="Resume a paused pipeline")
async def resume_pipeline(pipeline_id: str):
    engine = _engines.get(pipeline_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No running engine for this pipeline")
    await engine.resume()
    return {"status": engine.pipeline.status.value, "pipeline_id": pipeline_id}


# ────────────────────────────────────────────────────────────────────
# Metrics & SSE
# ────────────────────────────────────────────────────────────────────

@router.get("/pipelines/{pipeline_id}/metrics", summary="Get current metrics")
async def get_metrics(pipeline_id: str):
    engine = _engines.get(pipeline_id)
    if not engine:
        pipe = _pipelines.get(pipeline_id)
        if not pipe:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return StreamMetrics(pipeline_id=pipeline_id).model_dump()
    return engine.metrics.model_dump()


@router.get("/pipelines/{pipeline_id}/stream", summary="SSE stream of metrics + window events")
async def stream_events(pipeline_id: str):
    engine = _engines.get(pipeline_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No running engine for this pipeline")

    sse_sink = engine.get_sse_sink()
    if not sse_sink:
        raise HTTPException(status_code=409, detail="Pipeline has no SSE sink configured")

    client_id = uuid.uuid4().hex[:8]
    queue = sse_sink.subscribe(client_id)

    async def event_generator():
        try:
            while True:
                payload = await queue.get()
                if payload is None:
                    break
                yield f"data: {payload}\n\n"
        finally:
            sse_sink.unsubscribe(client_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ────────────────────────────────────────────────────────────────────
# Templates
# ────────────────────────────────────────────────────────────────────

@router.get("/templates", summary="Get streaming pipeline templates")
async def list_templates():
    return {
        "templates": [
            {
                "id": "ecommerce_orders",
                "name": "E-Commerce Order Stream",
                "description": "Simulated order events with 60s tumbling windows, tracking revenue and order count per region.",
                "tags": ["demo", "ecommerce", "tumbling"],
                "pipeline": {
                    "name": "E-Commerce Order Stream",
                    "description": "Real-time order analytics with tumbling windows",
                    "source": {"type": "simulated", "config": {"event_type": "orders", "events_per_second": 10, "num_keys": 5}},
                    "event_time_field": "timestamp",
                    "watermark_delay_seconds": 10,
                    "window": {"type": "tumbling", "size_seconds": 60, "late_data_policy": "drop", "allowed_lateness_seconds": 10},
                    "transforms": [
                        {"type": "key_by", "description": "Group by region", "config": {"field": "region"}},
                        {"type": "aggregate", "description": "Sum revenue & count orders", "config": {"fields": [{"field": "amount", "function": "SUM"}, {"field": "amount", "function": "COUNT"}]}},
                    ],
                    "sinks": [
                        {"type": "sse", "config": {}},
                        {"type": "console", "config": {}},
                    ],
                    "checkpoint_interval_seconds": 30,
                    "tags": ["demo", "ecommerce"],
                },
            },
            {
                "id": "iot_sensors",
                "name": "IoT Sensor Monitoring",
                "description": "Sliding window over sensor readings. Alerts when temperature exceeds threshold.",
                "tags": ["demo", "iot", "sliding", "alerts"],
                "pipeline": {
                    "name": "IoT Sensor Monitor",
                    "description": "Sliding window temperature monitoring with alerts",
                    "source": {"type": "simulated", "config": {"event_type": "sensors", "events_per_second": 20, "num_keys": 8, "schema": [{"name": "sensor_id", "type": "string"}, {"name": "temperature", "type": "float"}, {"name": "humidity", "type": "float"}, {"name": "location", "type": "choice:floor_1,floor_2,floor_3,roof"}]}},
                    "event_time_field": "timestamp",
                    "watermark_delay_seconds": 5,
                    "window": {"type": "sliding", "size_seconds": 120, "slide_seconds": 30, "late_data_policy": "drop"},
                    "transforms": [
                        {"type": "key_by", "description": "Group by location", "config": {"field": "location"}},
                        {"type": "aggregate", "description": "Avg temperature", "config": {"fields": [{"field": "temperature", "function": "AVG"}, {"field": "temperature", "function": "MAX"}]}},
                    ],
                    "sinks": [
                        {"type": "sse", "config": {}},
                        {"type": "alert", "config": {"rules": [{"field": "max_temperature", "operator": ">", "threshold": 85, "label": "High temperature"}]}},
                    ],
                    "tags": ["demo", "iot"],
                },
            },
            {
                "id": "session_analytics",
                "name": "User Session Analytics",
                "description": "Session windows that group user clickstream events by activity gaps.",
                "tags": ["demo", "session", "clickstream"],
                "pipeline": {
                    "name": "User Session Analytics",
                    "description": "Session-based clickstream analysis",
                    "source": {"type": "simulated", "config": {"event_type": "clicks", "events_per_second": 15, "num_keys": 20, "schema": [{"name": "user_id", "type": "string"}, {"name": "page", "type": "choice:/home,/products,/cart,/checkout,/profile"}, {"name": "action", "type": "choice:view,click,scroll,add_to_cart,purchase"}, {"name": "duration_ms", "type": "int"}]}},
                    "event_time_field": "timestamp",
                    "watermark_delay_seconds": 15,
                    "window": {"type": "session", "gap_seconds": 30, "late_data_policy": "update"},
                    "transforms": [
                        {"type": "key_by", "description": "Group by user", "config": {"field": "user_id"}},
                        {"type": "aggregate", "description": "Count page views per session", "config": {"fields": [{"field": "duration_ms", "function": "SUM"}, {"field": "page", "function": "COUNT"}]}},
                    ],
                    "sinks": [
                        {"type": "sse", "config": {}},
                        {"type": "console", "config": {}},
                    ],
                    "tags": ["demo", "sessions"],
                },
            },
            {
                "id": "file_watcher_etl",
                "name": "File Drop ETL",
                "description": "Watches a folder for new CSV files, applies tumbling windows to aggregate records.",
                "tags": ["etl", "file", "tumbling"],
                "pipeline": {
                    "name": "File Drop ETL",
                    "description": "Process CSV file drops in real-time",
                    "source": {"type": "file_watcher", "config": {"watch_dir": "data/uploads", "pattern": "*.csv", "poll_interval_seconds": 3}},
                    "event_time_field": "timestamp",
                    "watermark_delay_seconds": 30,
                    "window": {"type": "tumbling", "size_seconds": 300, "late_data_policy": "update", "allowed_lateness_seconds": 60},
                    "transforms": [],
                    "sinks": [
                        {"type": "sse", "config": {}},
                        {"type": "file", "config": {"output_dir": "data/streaming_output", "format": "json"}},
                    ],
                    "tags": ["etl", "file"],
                },
            },
        ]
    }
