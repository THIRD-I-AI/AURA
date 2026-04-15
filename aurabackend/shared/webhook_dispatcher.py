"""
Outbound Webhook Dispatcher
============================
Subscribes to the universal streaming bus and POSTs configured webhooks for
events users care about (pipeline.complete, agent.complete, uasr.drift, etc.).

Design
------
- One in-process subscriber on the wildcard pattern ``*`` that observes every
  StreamEvent the streaming_manager publishes.
- Each event is mapped to a canonical webhook ``event_type`` (e.g.
  ``pipeline.complete``) using a small classifier. Events that do not map to
  a webhook event_type are ignored.
- Registered subscriptions are loaded from a JSON file
  (``data/webhooks/subscriptions.json``) and written back on every CRUD op.
  Each subscription has: id, url, events (list of patterns or "*"),
  secret (optional, used to sign payloads), active flag, headers, retries.
- Delivery uses httpx.AsyncClient with bounded retries + exponential backoff.
  Outcomes (success/failure + status/error) are recorded into a small in-memory
  delivery log (last 200) so the API can expose a recent-deliveries view.
- HMAC-SHA256 signature header ``X-AURA-Signature`` is included when a secret
  is set, computed over the raw JSON body.

This module is intentionally cross-process unaware: it observes events from
the gateway's local streaming_manager. UASR / pipeline events that originate
in other processes already get re-published into the gateway via existing
pollers (see api_gateway/main.py UASR poller) and the in-process pipeline /
agent runners.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import httpx

from shared.streaming_manager import StreamEvent, streaming_manager

logger = logging.getLogger("aura.webhooks")


# ── Storage ────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "webhooks",
)
_STORE_PATH = os.path.join(_DATA_DIR, "subscriptions.json")


# ── Models ─────────────────────────────────────────────────────────

@dataclass
class WebhookSubscription:
    id: str
    url: str
    events: List[str]                 # patterns: "pipeline.complete", "agent.*", "*"
    secret: Optional[str] = None
    active: bool = True
    headers: Dict[str, str] = field(default_factory=dict)
    retries: int = 3
    description: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def matches(self, event_type: str) -> bool:
        for pat in self.events:
            if pat == "*" or pat == event_type:
                return True
            if pat.endswith(".*") and event_type.startswith(pat[:-1]):
                return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        # Don't leak the secret in responses; redact in serializer
        return d


@dataclass
class DeliveryRecord:
    id: str
    subscription_id: str
    event_type: str
    url: str
    status: str                       # "success" | "failed"
    http_status: Optional[int]
    attempts: int
    error: Optional[str]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Event classifier ───────────────────────────────────────────────

def _classify(event: StreamEvent) -> Optional[str]:
    """Map a StreamEvent → canonical webhook event_type, or None to skip."""
    topic = event.topic
    et = event.event_type
    ns = topic.split(":", 1)[0] if ":" in topic else topic

    # Lifecycle for major namespaces
    if ns in ("pipeline", "agent", "etl", "query", "upload"):
        if et == "complete":
            return f"{ns}.complete"
        if et == "error":
            return f"{ns}.failed"
        # Skip progress / data / heartbeat — too noisy for webhooks
        return None

    # UASR drift events — drift status payload comes as 'data' on uasr:metrics
    if ns == "uasr":
        if et == "data" and isinstance(event.payload, dict) and event.payload.get("drift_detected"):
            return "uasr.drift"
        if et == "complete":
            return "uasr.recovered"
        return None

    # Inbound hook fired
    if ns == "hooks" and et == "complete":
        return "hook.fired"

    # System health degradation
    if ns == "system" and et == "data":
        overall = (event.payload or {}).get("overall")
        if overall and overall != "healthy":
            return "system.degraded"
        return None

    return None


# ── Dispatcher ─────────────────────────────────────────────────────

class WebhookDispatcher:
    _MAX_LOG = 200

    def __init__(self) -> None:
        self._subs: Dict[str, WebhookSubscription] = {}
        self._log: deque[DeliveryRecord] = deque(maxlen=self._MAX_LOG)
        self._sub_id: Optional[str] = None
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._client: Optional[httpx.AsyncClient] = None
        self._load()

    # ── Persistence ────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(_STORE_PATH):
            return
        try:
            with open(_STORE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for r in raw:
                self._subs[r["id"]] = WebhookSubscription(**r)
            logger.info("Loaded %d webhook subscriptions", len(self._subs))
        except Exception as exc:
            logger.warning("Failed to load webhook store: %s", exc)

    def _save(self) -> None:
        os.makedirs(_DATA_DIR, exist_ok=True)
        try:
            with open(_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump([s.__dict__ for s in self._subs.values()], f, indent=2)
        except Exception as exc:
            logger.warning("Failed to persist webhook store: %s", exc)

    # ── CRUD ───────────────────────────────────────────────────────

    def list(self) -> List[WebhookSubscription]:
        return list(self._subs.values())

    def get(self, sub_id: str) -> Optional[WebhookSubscription]:
        return self._subs.get(sub_id)

    def register(
        self,
        url: str,
        events: Iterable[str],
        secret: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        retries: int = 3,
        description: str = "",
    ) -> WebhookSubscription:
        sub = WebhookSubscription(
            id=uuid.uuid4().hex,
            url=url,
            events=list(events) or ["*"],
            secret=secret,
            headers=headers or {},
            retries=max(0, min(retries, 10)),
            description=description,
        )
        self._subs[sub.id] = sub
        self._save()
        return sub

    def update(self, sub_id: str, **fields) -> Optional[WebhookSubscription]:
        sub = self._subs.get(sub_id)
        if not sub:
            return None
        for k, v in fields.items():
            if hasattr(sub, k) and v is not None:
                setattr(sub, k, v)
        self._save()
        return sub

    def delete(self, sub_id: str) -> bool:
        existed = self._subs.pop(sub_id, None) is not None
        if existed:
            self._save()
        return existed

    def deliveries(self) -> List[DeliveryRecord]:
        return list(self._log)

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._sub_id, self._queue = streaming_manager.subscribe("*")
        self._client = httpx.AsyncClient(timeout=10)
        self._task = asyncio.create_task(self._run())
        logger.info("Webhook dispatcher started (subs=%d)", len(self._subs))

    async def stop(self) -> None:
        self._stop.set()
        if self._sub_id:
            streaming_manager.unsubscribe(self._sub_id)
            self._sub_id = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Webhook dispatcher stopped")

    # ── Main loop ──────────────────────────────────────────────────

    async def _run(self) -> None:
        assert self._queue is not None
        while not self._stop.is_set():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.warning("Webhook subscriber error: %s", exc)
                continue

            event_type = _classify(event)
            if event_type is None:
                continue

            matched = [s for s in self._subs.values() if s.active and s.matches(event_type)]
            if not matched:
                continue

            payload = self._build_payload(event_type, event)
            for sub in matched:
                # Fire-and-forget per subscription so a slow target doesn't block others
                asyncio.create_task(self._deliver(sub, event_type, payload))

    # ── Delivery ───────────────────────────────────────────────────

    @staticmethod
    def _build_payload(event_type: str, event: StreamEvent) -> Dict[str, Any]:
        return {
            "id": event.event_id,
            "event": event_type,
            "topic": event.topic,
            "timestamp": event.timestamp,
            "data": event.payload,
        }

    async def _deliver(
        self,
        sub: WebhookSubscription,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        assert self._client is not None
        body = json.dumps(payload, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AURA-Webhooks/1.0",
            "X-AURA-Event": event_type,
            "X-AURA-Delivery": uuid.uuid4().hex,
            **sub.headers,
        }
        if sub.secret:
            sig = hmac.new(sub.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-AURA-Signature"] = f"sha256={sig}"

        attempts = 0
        last_status: Optional[int] = None
        last_err: Optional[str] = None
        ok = False

        while attempts <= sub.retries:
            attempts += 1
            try:
                resp = await self._client.post(sub.url, content=body, headers=headers)
                last_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    ok = True
                    break
                last_err = f"HTTP {resp.status_code}"
            except Exception as exc:
                last_err = str(exc)

            if attempts <= sub.retries:
                await asyncio.sleep(min(30.0, 0.5 * (2 ** (attempts - 1))))

        record = DeliveryRecord(
            id=uuid.uuid4().hex,
            subscription_id=sub.id,
            event_type=event_type,
            url=sub.url,
            status="success" if ok else "failed",
            http_status=last_status,
            attempts=attempts,
            error=None if ok else last_err,
        )
        self._log.append(record)
        if not ok:
            logger.warning(
                "Webhook delivery failed sub=%s event=%s url=%s err=%s",
                sub.id[:8], event_type, sub.url, last_err,
            )

        # Publish delivery event so the frontend can live-refresh. Do not
        # route this through _classify → no feedback loop into outbound
        # webhook delivery.
        try:
            await streaming_manager.publish(StreamEvent(
                topic="webhooks:deliveries",
                event_type="data",
                payload=record.__dict__,
            ))
        except Exception:
            pass

    async def fire_test(self, sub_id: str, event_type: str = "test.ping") -> Optional[DeliveryRecord]:
        """Manually trigger a test delivery for a registered subscription."""
        sub = self._subs.get(sub_id)
        if not sub:
            return None
        payload = {
            "id": uuid.uuid4().hex,
            "event": event_type,
            "topic": "test:manual",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {"message": "AURA webhook test ping", "ts": time.time()},
        }
        await self._deliver(sub, event_type, payload)
        return self._log[-1] if self._log else None


# ── Singleton ─────────────────────────────────────────────────────

webhook_dispatcher = WebhookDispatcher()
