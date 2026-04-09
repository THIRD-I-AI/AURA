"""
Universal Streaming Manager
=============================
Single in-process pub/sub bus that powers all SSE streams.

Every async operation (query execution, file upload, ETL run, agent
execution, UASR recovery, pipeline monitor) publishes events here.
The frontend subscribes once via GET /stream/{topic} and receives
real-time progress, data, and completion events for any topic.

Topic namespaces::

    query:job_123        — SQL execution progress
    upload:file_abc      — file upload & profiling progress
    etl:run_def          — ETL pipeline execution
    agent:run_ghi        — agent DAG execution (mirrors /agent/execute/stream)
    pipeline:pipe_jkl    — streaming pipeline metrics
    uasr:source_mno      — UASR drift detection & recovery
    monitor:*            — system health & alert events
    system:health        — periodic health snapshot broadcast

Wildcard subscriptions::

    monitor:*   — matches any topic starting with "monitor:"
    *           — matches ALL topics (use with care)

Usage::

    # Publisher (in a router or background task):
    from shared.streaming_manager import streaming_manager, TOPIC_QUERY
    await streaming_manager.publish_progress(TOPIC_QUERY, job_id, "Running SQL", 0.5)
    await streaming_manager.publish_complete(TOPIC_QUERY, job_id, result)

    # Consumer (in stream.py):
    sub_id, queue = streaming_manager.subscribe("query:job_123")
    event = await queue.get()          # StreamEvent
    streaming_manager.unsubscribe(sub_id)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("aura.streaming")

# ── Topic namespace constants ──────────────────────────────────────

TOPIC_QUERY    = "query"
TOPIC_UPLOAD   = "upload"
TOPIC_ETL      = "etl"
TOPIC_AGENT    = "agent"
TOPIC_PIPELINE = "pipeline"
TOPIC_UASR     = "uasr"
TOPIC_MONITOR  = "monitor"
TOPIC_SYSTEM   = "system"


# ── Event model ────────────────────────────────────────────────────

@dataclass
class StreamEvent:
    topic: str
    event_type: str   # "progress" | "complete" | "error" | "data" | "heartbeat"
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_sse(self) -> str:
        """Serialise as a Server-Sent Event string."""
        import json
        data = json.dumps({
            "topic": self.topic,
            "type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        })
        return (
            f"id: {self.event_id}\n"
            f"event: {self.event_type}\n"
            f"data: {data}\n\n"
        )


# ── Manager ────────────────────────────────────────────────────────

class StreamingManager:
    """
    Fanout pub/sub bus.

    - Subscriptions are keyed by a (sub_id, topic_pattern) pair.
    - Each subscriber gets its own asyncio.Queue (max 200 items, drops oldest).
    - Topics are matched by exact string or prefix wildcard ("prefix:*").
    - Events are also buffered per-topic (last 50) for Last-Event-ID replay.
    """

    _MAX_QUEUE  = 200
    _BUFFER_LEN = 50

    def __init__(self) -> None:
        # sub_id → (pattern, queue)
        self._subscribers: Dict[str, Tuple[str, asyncio.Queue]] = {}
        # topic → deque of recent events (for replay)
        self._buffers: Dict[str, List[StreamEvent]] = {}
        self._lock = asyncio.Lock()

    # ── Subscribe / Unsubscribe ────────────────────────────────────

    def subscribe(self, topic_pattern: str) -> Tuple[str, asyncio.Queue]:
        """
        Register a subscriber.
        Returns (subscriber_id, queue).
        """
        sub_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._MAX_QUEUE)
        self._subscribers[sub_id] = (topic_pattern, queue)
        logger.debug("New subscriber %s for pattern '%s'", sub_id[:8], topic_pattern)
        return sub_id, queue

    def unsubscribe(self, sub_id: str) -> None:
        self._subscribers.pop(sub_id, None)
        logger.debug("Subscriber %s disconnected", sub_id[:8])

    def subscriber_count(self, topic: Optional[str] = None) -> int:
        if topic is None:
            return len(self._subscribers)
        return sum(
            1 for pat, _ in self._subscribers.values()
            if self._matches(topic, pat)
        )

    # ── Publish ────────────────────────────────────────────────────

    async def publish(self, event: StreamEvent) -> None:
        """Fanout event to all matching subscriber queues."""
        # Buffer event for replay
        buf = self._buffers.setdefault(event.topic, [])
        buf.append(event)
        if len(buf) > self._BUFFER_LEN:
            buf.pop(0)

        # Fanout to subscribers
        for sub_id, (pattern, queue) in list(self._subscribers.items()):
            if not self._matches(event.topic, pattern):
                continue
            try:
                if queue.full():
                    try:
                        queue.get_nowait()  # drop oldest
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(event)
            except Exception as exc:
                logger.warning("Failed to deliver to subscriber %s: %s", sub_id[:8], exc)

    def get_buffered_events(
        self,
        topic: str,
        after_event_id: Optional[str] = None,
    ) -> List[StreamEvent]:
        """Return buffered events for Last-Event-ID replay."""
        buf = self._buffers.get(topic, [])
        if after_event_id is None:
            return list(buf)
        found = False
        result = []
        for ev in buf:
            if found:
                result.append(ev)
            if ev.event_id == after_event_id:
                found = True
        return result

    # ── Convenience publishers ─────────────────────────────────────

    async def publish_progress(
        self,
        namespace: str,
        job_id: str,
        message: str,
        percent: float,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self.publish(StreamEvent(
            topic=f"{namespace}:{job_id}",
            event_type="progress",
            payload={
                "message": message,
                "percent": round(percent * 100) if percent <= 1.0 else round(percent),
                "job_id": job_id,
                **(extra or {}),
            },
        ))

    async def publish_complete(
        self,
        namespace: str,
        job_id: str,
        result: Dict[str, Any],
    ) -> None:
        await self.publish(StreamEvent(
            topic=f"{namespace}:{job_id}",
            event_type="complete",
            payload={"job_id": job_id, "result": result},
        ))

    async def publish_error(
        self,
        namespace: str,
        job_id: str,
        error: str,
        code: str = "OPERATION_FAILED",
    ) -> None:
        await self.publish(StreamEvent(
            topic=f"{namespace}:{job_id}",
            event_type="error",
            payload={"job_id": job_id, "error": error, "code": code},
        ))

    async def broadcast(
        self,
        namespace: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Broadcast to the namespace wildcard topic (e.g. monitor:* subscribers)."""
        await self.publish(StreamEvent(
            topic=f"{namespace}:broadcast",
            event_type=event_type,
            payload=payload,
        ))

    # ── Internal ──────────────────────────────────────────────────

    @staticmethod
    def _matches(topic: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith(":*"):
            prefix = pattern[:-1]   # strip the *
            return topic.startswith(prefix)
        return topic == pattern


# ── Singleton ─────────────────────────────────────────────────────

streaming_manager = StreamingManager()
