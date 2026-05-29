"""
WebSocket Source Adapter
=========================
Connects to a WebSocket endpoint and buffers inbound messages for the
streaming engine's micro-batch loop.

Config options:
  url:         str   – ws:// or wss:// endpoint (required)
  headers:     dict | str(JSON) – optional additional headers
  subprotocols: list | str(CSV) – optional subprotocols
  key_field:   str   – event data field to use as the partition key (optional)
  ping_interval:   float – keepalive ping interval in seconds (default: 20)
  connect_timeout: float – seconds to wait for initial connection before retrying (default: 10)
  reconnect:       bool  – auto-reconnect with exponential backoff (default: True)
  max_buffer:      int   – drop oldest once buffered messages exceed this (default: 10000)

Requires the ``websockets`` package.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from pipeline.streaming.models import StreamEvent
from pipeline.streaming.sources.base import BaseSource

logger = logging.getLogger("aura.streaming.source.websocket")


def _parse_headers(raw: Any) -> Dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            pass
    return {}


def _parse_subprotocols(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []


class WebSocketSource(BaseSource):
    """Consumes streaming events from a WebSocket endpoint."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._url: str = config["url"]
        self._headers: Dict[str, str] = _parse_headers(config.get("headers"))
        self._subprotocols: List[str] = _parse_subprotocols(config.get("subprotocols"))
        self._key_field: Optional[str] = config.get("key_field") or None
        self._ping_interval: float = float(config.get("ping_interval", 20))
        self._connect_timeout: float = float(config.get("connect_timeout", 10))
        self._reconnect: bool = bool(config.get("reconnect", True))
        self._max_buffer: int = int(config.get("max_buffer", 10_000))

        self._queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=self._max_buffer)
        self._reader_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._received: int = 0
        self._dropped: int = 0

    async def start(self) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "websockets is required for WebSocketSource. "
                "Install it with: pip install websockets"
            ) from exc

        self._stop.clear()
        self._running = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info("WebSocket source started: url=%s", self._url)

    async def stop(self) -> None:
        self._running = False
        self._stop.set()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        logger.info("WebSocket source stopped (received=%d, dropped=%d)",
                    self._received, self._dropped)

    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        if not self._running:
            return []
        events: List[StreamEvent] = []
        for _ in range(max_events):
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    def get_offsets(self) -> Dict[str, Any]:
        return {"received": self._received, "dropped": self._dropped}

    # ── Internal ───────────────────────────────────────────────────

    async def _reader_loop(self) -> None:
        import websockets
        backoff = 1.0
        while not self._stop.is_set():
            try:
                connect_kwargs: Dict[str, Any] = {"ping_interval": self._ping_interval}
                if self._headers:
                    connect_kwargs["additional_headers"] = list(self._headers.items())
                if self._subprotocols:
                    connect_kwargs["subprotocols"] = self._subprotocols

                async with await asyncio.wait_for(
                    websockets.connect(self._url, **connect_kwargs),
                    timeout=self._connect_timeout,
                ) as ws:
                    logger.info("WebSocket connected: %s", self._url)
                    backoff = 1.0
                    async for raw_msg in ws:
                        if self._stop.is_set():
                            break
                        self._enqueue(raw_msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WebSocket error on %s: %s", self._url, exc)
                if not self._reconnect or self._stop.is_set():
                    return
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    return
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, 30.0)

    def _enqueue(self, raw_msg: Any) -> None:
        if isinstance(raw_msg, (bytes, bytearray)):
            try:
                raw_msg = raw_msg.decode("utf-8")
            except Exception:
                raw_msg = str(raw_msg)

        data: Dict[str, Any]
        try:
            parsed = json.loads(raw_msg) if isinstance(raw_msg, str) else raw_msg
            data = parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            data = {"raw": raw_msg if isinstance(raw_msg, str) else str(raw_msg)}

        event_time = float(data.get("timestamp", time.time())) if isinstance(data, dict) else time.time()
        key = None
        if self._key_field and isinstance(data, dict):
            key = str(data.get(self._key_field)) if data.get(self._key_field) is not None else None

        event = StreamEvent(
            timestamp=event_time,
            key=key,
            data=data,
            source=f"ws://{self._url}",
        )

        try:
            self._queue.put_nowait(event)
            self._received += 1
        except asyncio.QueueFull:
            # Drop oldest to keep up with fast producers
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(event)
                self._received += 1
                self._dropped += 1
            except asyncio.QueueFull:
                self._dropped += 1
