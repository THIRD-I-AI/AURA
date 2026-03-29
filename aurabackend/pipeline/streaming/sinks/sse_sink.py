"""
SSE Sink – Push window results to Server-Sent Events channel
=============================================================
Used by the frontend for real-time streaming dashboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Set

from pipeline.streaming.models import WindowState, StreamEvent
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.sse")


class SSESink(BaseSink):
    """Broadcasts closed window data to subscribed SSE clients."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._subscribers: Dict[str, asyncio.Queue] = {}

    async def start(self) -> None:
        self._running = True
        logger.info("SSE sink started")

    async def stop(self) -> None:
        self._running = False
        for q in self._subscribers.values():
            await q.put(None)  # sentinel to close generators
        self._subscribers.clear()
        logger.info("SSE sink stopped")

    def subscribe(self, client_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[client_id] = q
        logger.info("SSE client subscribed: %s (%d total)", client_id, len(self._subscribers))
        return q

    def unsubscribe(self, client_id: str) -> None:
        self._subscribers.pop(client_id, None)
        logger.info("SSE client unsubscribed: %s", client_id)

    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        payload = json.dumps({
            "type": "window_closed",
            "pipeline_id": pipeline_id,
            "window_key": window.window_key,
            "window_start": window.window_start.isoformat() if window.window_start else None,
            "window_end": window.window_end.isoformat() if window.window_end else None,
            "event_count": window.event_count,
            "aggregations": window.aggregations,
        })
        await self._broadcast(payload)

    async def emit_late_event(self, event: StreamEvent, pipeline_id: str) -> None:
        payload = json.dumps({
            "type": "late_event",
            "pipeline_id": pipeline_id,
            "event_id": event.event_id,
            "key": event.key,
            "timestamp": event.timestamp.isoformat(),
        })
        await self._broadcast(payload)

    async def emit_metrics(self, metrics: Dict[str, Any]) -> None:
        payload = json.dumps({"type": "metrics", **metrics})
        await self._broadcast(payload)

    # ── internal ──────────────────────────────────────────────
    async def _broadcast(self, payload: str) -> None:
        dead: list[str] = []
        for cid, q in self._subscribers.items():
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(cid)
        for cid in dead:
            self._subscribers.pop(cid, None)
            logger.warning("Dropped slow SSE client: %s", cid)
