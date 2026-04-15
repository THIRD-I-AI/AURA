"""
Webhooks Router
================
CRUD + test-fire endpoints for outbound webhook subscriptions.

Subscriptions are observed by ``shared.webhook_dispatcher`` which subscribes
to the streaming bus and POSTs matching events. The dispatcher is started in
the API gateway lifespan.

Routes
------
- POST   /webhooks                  register a new subscription
- GET    /webhooks                  list all subscriptions (secrets redacted)
- GET    /webhooks/{id}             fetch one
- PATCH  /webhooks/{id}             update fields (active, events, headers, …)
- DELETE /webhooks/{id}             unregister
- POST   /webhooks/{id}/test        fire a one-off test delivery
- GET    /webhooks/deliveries       recent delivery log (last 200)
- GET    /webhooks/events           list known event types
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from shared.logging_config import get_logger
from shared.webhook_dispatcher import WebhookSubscription, webhook_dispatcher

logger = get_logger("aura.api_gateway.webhooks")

router = APIRouter(tags=["Webhooks"])


# ── Models ─────────────────────────────────────────────────────────

class WebhookCreateRequest(BaseModel):
    url: str
    events: List[str] = Field(default_factory=lambda: ["*"])
    secret: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    retries: int = 3
    description: str = ""


class WebhookUpdateRequest(BaseModel):
    url: Optional[str] = None
    events: Optional[List[str]] = None
    secret: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    retries: Optional[int] = None
    active: Optional[bool] = None
    description: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────

_KNOWN_EVENTS = [
    "pipeline.complete", "pipeline.failed",
    "agent.complete", "agent.failed",
    "etl.complete", "etl.failed",
    "query.complete", "query.failed",
    "upload.complete", "upload.failed",
    "uasr.drift", "uasr.recovered",
    "system.degraded",
]


def _serialize(sub: WebhookSubscription) -> Dict[str, Any]:
    d = sub.__dict__.copy()
    if d.get("secret"):
        d["secret"] = "***redacted***"
        d["has_secret"] = True
    else:
        d["has_secret"] = False
    return d


# ── Routes ─────────────────────────────────────────────────────────

@router.post("/webhooks")
async def create_webhook(req: WebhookCreateRequest) -> Dict[str, Any]:
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    sub = webhook_dispatcher.register(
        url=req.url,
        events=req.events,
        secret=req.secret,
        headers=req.headers,
        retries=req.retries,
        description=req.description,
    )
    return {"status": "success", "webhook": _serialize(sub)}


@router.get("/webhooks")
async def list_webhooks() -> Dict[str, Any]:
    return {
        "status": "success",
        "webhooks": [_serialize(s) for s in webhook_dispatcher.list()],
    }


@router.get("/webhooks/events")
async def list_known_events() -> Dict[str, Any]:
    return {"status": "success", "events": _KNOWN_EVENTS}


@router.get("/webhooks/deliveries")
async def list_deliveries() -> Dict[str, Any]:
    return {
        "status": "success",
        "deliveries": [d.__dict__ for d in webhook_dispatcher.deliveries()],
    }


@router.get("/webhooks/{sub_id}")
async def get_webhook(sub_id: str) -> Dict[str, Any]:
    sub = webhook_dispatcher.get(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "webhook": _serialize(sub)}


@router.patch("/webhooks/{sub_id}")
async def update_webhook(sub_id: str, req: WebhookUpdateRequest) -> Dict[str, Any]:
    sub = webhook_dispatcher.update(sub_id, **req.model_dump(exclude_none=True))
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "webhook": _serialize(sub)}


@router.delete("/webhooks/{sub_id}")
async def delete_webhook(sub_id: str) -> Dict[str, Any]:
    if not webhook_dispatcher.delete(sub_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "deleted": sub_id}


@router.post("/webhooks/{sub_id}/test")
async def test_webhook(sub_id: str) -> Dict[str, Any]:
    record = await webhook_dispatcher.fire_test(sub_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "delivery": record.__dict__}
