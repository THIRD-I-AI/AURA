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

import asyncio
import ipaddress
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.routers.workspaces import current_workspace_id
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
    "hook.fired",
]


def _is_ssrf_safe_url(url: str) -> bool:
    """BUG-054: reject a webhook URL whose host is (or resolves to) a
    loopback/link-local/private/reserved address.

    Without this, any authenticated tenant could register a URL like
    169.254.169.254 (cloud instance metadata) or 127.0.0.1:<internal-port>
    and use the backend as an SSRF proxy -- both the recurring dispatcher
    (shared/webhook_dispatcher.py) and POST /webhooks/{id}/test fire real
    outbound requests from the trusted backend's network position.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False

    try:
        ip = ipaddress.ip_address(host)
        candidates = [ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]

    return not any(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        for ip in candidates
    )


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
async def create_webhook(req: WebhookCreateRequest, request: Request) -> Dict[str, Any]:
    if not await asyncio.to_thread(_is_ssrf_safe_url, req.url):
        raise HTTPException(status_code=400, detail="url must be a public http(s) address")
    sub = await asyncio.to_thread(
        webhook_dispatcher.register,
        workspace_id=current_workspace_id(request),
        url=req.url,
        events=req.events,
        secret=req.secret,
        headers=req.headers,
        retries=req.retries,
        description=req.description,
    )
    return {"status": "success", "webhook": _serialize(sub)}


@router.get("/webhooks")
async def list_webhooks(request: Request) -> Dict[str, Any]:
    return {
        "status": "success",
        "webhooks": [_serialize(s) for s in webhook_dispatcher.list(current_workspace_id(request))],
    }


@router.get("/webhooks/events")
async def list_known_events() -> Dict[str, Any]:
    return {"status": "success", "events": _KNOWN_EVENTS}


@router.get("/webhooks/deliveries")
async def list_deliveries(request: Request) -> Dict[str, Any]:
    return {
        "status": "success",
        "deliveries": [d.__dict__ for d in webhook_dispatcher.deliveries(current_workspace_id(request))],
    }


@router.get("/webhooks/{sub_id}")
async def get_webhook(sub_id: str, request: Request) -> Dict[str, Any]:
    sub = webhook_dispatcher.get(sub_id, current_workspace_id(request))
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "webhook": _serialize(sub)}


@router.patch("/webhooks/{sub_id}")
async def update_webhook(sub_id: str, req: WebhookUpdateRequest, request: Request) -> Dict[str, Any]:
    if req.url is not None and not await asyncio.to_thread(_is_ssrf_safe_url, req.url):
        raise HTTPException(status_code=400, detail="url must be a public http(s) address")
    sub = await asyncio.to_thread(
        webhook_dispatcher.update,
        sub_id, current_workspace_id(request), **req.model_dump(exclude_none=True),
    )
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "webhook": _serialize(sub)}


@router.delete("/webhooks/{sub_id}")
async def delete_webhook(sub_id: str, request: Request) -> Dict[str, Any]:
    if not await asyncio.to_thread(webhook_dispatcher.delete, sub_id, current_workspace_id(request)):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "deleted": sub_id}


@router.post("/webhooks/{sub_id}/test")
async def test_webhook(sub_id: str, request: Request) -> Dict[str, Any]:
    record = await webhook_dispatcher.fire_test(sub_id, current_workspace_id(request))
    if record is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "success", "delivery": record.__dict__}
