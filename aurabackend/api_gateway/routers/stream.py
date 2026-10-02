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

from api_gateway.routers.workspaces import current_workspace_id
from shared.streaming_manager import StreamEvent, streaming_manager

logger = logging.getLogger("aura.stream")

router = APIRouter(tags=["Streaming"])

_HEARTBEAT_INTERVAL = 20  # seconds


async def _event_generator(
    topic: str,
    last_event_id: Optional[str],
    request: Request,
    replay_all: bool = False,
) -> AsyncGenerator[str, None]:
    """Core generator: subscribes, replays missed events, then streams live.

    The replay snapshot is always taken AFTER subscribing, with no await in between,
    so an event published while the replay is being sent is already in this client's
    queue. BUG-244: replay=true used to send the buffer first and subscribe afterwards;
    anything published in that window -- e.g. the 'complete' of a short run -- was in
    neither, and the UI spinner never stopped."""
    workspace_id = current_workspace_id(request)
    sub_id, queue = streaming_manager.subscribe(topic, workspace_id)
    logger.debug("SSE client connected to topic '%s' (sub=%s)", topic, sub_id[:8])

    try:
        # ── Replay buffered events: after Last-Event-ID, or all of them ──
        if last_event_id:
            missed = streaming_manager.get_buffered_events(
                topic, workspace_id, after_event_id=last_event_id,
            )
        elif replay_all:
            missed = streaming_manager.get_buffered_events(topic, workspace_id)
        else:
            missed = []
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
    return StreamingResponse(
        _event_generator(topic, last_event_id, request, replay_all=replay and not last_event_id),
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
