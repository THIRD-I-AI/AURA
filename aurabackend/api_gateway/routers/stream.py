"""
Universal SSE Stream Router
============================
Single endpoint that powers all real-time updates in the frontend.

GET /stream/{topic}
  - Subscribes to the given topic on the StreamingManager.
  - Streams events as `text/event-stream` until the client disconnects.
  - Supports Last-Event-ID header for missed-event replay.
  - Sends heartbeat pings every 20 s to prevent proxy timeouts.

Topic examples::

    query:job_123          — SQL execution progress
    upload:file_abc        — file upload & profiling
    etl:run_def            — ETL pipeline execution
    agent:run_ghi          — agent DAG execution
    pipeline:pipe_jkl      — streaming pipeline metrics
    uasr:source_mno        — UASR drift & recovery
    monitor:*              — all monitor events (wildcard)
    system:health          — periodic health snapshot
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from shared.streaming_manager import streaming_manager, StreamEvent

logger = logging.getLogger("aura.stream")

router = APIRouter(tags=["Streaming"])

_HEARTBEAT_INTERVAL = 20  # seconds


async def _event_generator(
    topic: str,
    last_event_id: Optional[str],
    request: Request,
) -> AsyncGenerator[str, None]:
    """Core generator: subscribes, replays missed events, then streams live."""
    sub_id, queue = streaming_manager.subscribe(topic)
    logger.debug("SSE client connected to topic '%s' (sub=%s)", topic, sub_id[:8])

    try:
        # ── Replay buffered events if Last-Event-ID supplied ───────────
        if last_event_id:
            missed = streaming_manager.get_buffered_events(topic, after_event_id=last_event_id)
            for ev in missed:
                yield ev.to_sse()

        # ── Live stream ────────────────────────────────────────────────
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                event: StreamEvent = await asyncio.wait_for(
                    queue.get(), timeout=_HEARTBEAT_INTERVAL
                )
                yield event.to_sse()
            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                yield ": heartbeat\n\n"

    except asyncio.CancelledError:
        pass
    finally:
        streaming_manager.unsubscribe(sub_id)
        logger.debug("SSE client disconnected from topic '%s' (sub=%s)", topic, sub_id[:8])


@router.get("/stream/{topic:path}")
async def stream_topic(
    topic: str,
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    replay: bool = Query(False, description="Replay all buffered events on connect"),
) -> StreamingResponse:
    """
    Universal SSE endpoint.

    Subscribe by navigating to `/stream/<topic>` in an EventSource.

    Wildcard subscriptions are supported::

        /stream/monitor:*   — all monitor events
        /stream/*           — every event on the bus
    """
    # For replay=true without a Last-Event-ID, send all buffered events
    effective_last_id: Optional[str] = last_event_id
    if replay and not last_event_id:
        # Use a sentinel that will never match, so all buffered events are returned
        buf = streaming_manager.get_buffered_events(topic)
        if buf:
            effective_last_id = None  # will be handled by replay=True path below

    async def _gen() -> AsyncGenerator[str, None]:
        if replay and not last_event_id:
            # Replay ALL buffered events
            for ev in streaming_manager.get_buffered_events(topic):
                yield ev.to_sse()
        async for chunk in _event_generator(topic, effective_last_id, request):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable Nginx buffering
            "Connection": "keep-alive",
        },
    )


@router.get("/stream")
async def list_stream_topics():
    """
    Return current subscriber counts by topic.
    Useful for the admin dashboard to see what the frontend is listening to.
    """
    return {
        "total_subscribers": streaming_manager.subscriber_count(),
        "note": "Subscribe with GET /stream/{topic} using EventSource",
        "example_topics": [
            "system:health",
            "query:{job_id}",
            "upload:{file_id}",
            "etl:{run_id}",
            "agent:{run_id}",
            "monitor:*",
        ],
    }
