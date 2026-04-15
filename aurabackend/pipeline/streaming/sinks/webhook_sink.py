"""
Webhook Sink – POST window results to an HTTP endpoint
=======================================================
Emits each closed window as a JSON POST to a user-provided URL.

Config:
  url:           str   – target endpoint (required)
  secret:        str   – optional HMAC-SHA256 signing key; signature sent in
                          X-AURA-Signature: sha256=<hex>
  headers:       dict  – additional HTTP headers
  timeout_s:     float – per-request timeout (default 10)
  retries:       int   – retry count on failure (default 2, exp backoff)
  include_late:  bool  – also POST late events to the same URL (default False)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import httpx

from pipeline.streaming.models import StreamEvent, WindowState
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.webhook")


class WebhookSink(BaseSink):
    """POSTs window results as JSON to a configured URL."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._url: str = config["url"]
        self._secret: Optional[str] = config.get("secret") or None
        self._headers: Dict[str, str] = dict(config.get("headers") or {})
        self._timeout: float = float(config.get("timeout_s", 10))
        self._retries: int = int(config.get("retries", 2))
        raw_late = config.get("include_late", False)
        self._include_late: bool = (
            raw_late if isinstance(raw_late, bool)
            else str(raw_late).strip().lower() in ("true", "1", "yes")
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        self._running = True
        logger.info("Webhook sink started → %s", self._url)

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Webhook sink stopped")

    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        payload = {
            "event": "window.closed",
            "pipeline_id": pipeline_id,
            "window_key": window.window_key,
            "window_start": window.window_start,
            "window_end": window.window_end,
            "event_count": window.event_count,
            "aggregations": window.aggregations,
        }
        await self._post(payload)

    async def emit_late_event(self, event: StreamEvent, pipeline_id: str) -> None:
        if not self._include_late:
            return
        await self._post({
            "event": "late_event",
            "pipeline_id": pipeline_id,
            "event_time": event.event_time,
            "data": event.data,
        })

    async def _post(self, payload: Dict[str, Any]) -> None:
        if self._client is None:
            return
        body = json.dumps(payload, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AURA-Streaming/1.0",
            **self._headers,
        }
        if self._secret:
            sig = hmac.new(self._secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-AURA-Signature"] = f"sha256={sig}"

        attempts = 0
        while attempts <= self._retries:
            attempts += 1
            try:
                resp = await self._client.post(self._url, content=body, headers=headers)
                if 200 <= resp.status_code < 300:
                    return
                logger.warning(
                    "Webhook sink non-2xx (%s) attempt=%d url=%s",
                    resp.status_code, attempts, self._url,
                )
            except Exception as exc:
                logger.warning(
                    "Webhook sink error attempt=%d url=%s err=%s",
                    attempts, self._url, exc,
                )
            if attempts <= self._retries:
                await asyncio.sleep(min(10.0, 0.5 * (2 ** (attempts - 1))))
