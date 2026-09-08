"""POST /webhooks and PATCH /webhooks/{id} must not block the single uvicorn
worker.

WHY THIS EXISTS: create_webhook called webhook_dispatcher.register(...)
directly, and update_webhook called webhook_dispatcher.update(...) directly.
Both are synchronous and end with self._save(), which does a blocking
`open(..., 'w')` + `json.dump()` of the entire subscription store on every
call, with no asyncio.to_thread offload. Under the repo's single-worker
constraint (.claude/rules/backend.md "Async safety"), that blocking disk
write inside an async route handler freezes every concurrent request for
every tenant until it returns.

These tests prove the fix by asserting the dispatcher's synchronous
register()/update() run on a worker thread, not on the event loop thread
that is handling the request.
"""
from __future__ import annotations

import os
import sys
import threading

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.webhooks import (
    WebhookCreateRequest,
    WebhookUpdateRequest,
    create_webhook,
    update_webhook,
)
from shared.webhook_dispatcher import WebhookSubscription, webhook_dispatcher


def _request(tenant: str) -> Request:
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/webhooks",
        "headers": [],
        "query_string": b"",
    })
    request.state.user = {"org_id": tenant}
    return request


@pytest.mark.asyncio
async def test_create_webhook_offloads_register_to_worker_thread(monkeypatch):
    main_thread = threading.current_thread()
    call_threads: list[threading.Thread] = []

    def fake_register(**kwargs) -> WebhookSubscription:
        # This stands in for the real register()->_save() blocking disk
        # write: recording which thread executed it.
        call_threads.append(threading.current_thread())
        return WebhookSubscription(
            id="sub-1",
            url=kwargs["url"],
            events=kwargs["events"],
            secret=kwargs.get("secret"),
            headers=kwargs.get("headers") or {},
            retries=kwargs.get("retries", 3),
            description=kwargs.get("description", ""),
            workspace_id=kwargs["workspace_id"],
        )

    monkeypatch.setattr(webhook_dispatcher, "register", fake_register)

    req = WebhookCreateRequest(url="http://example.com/hook", events=["*"])
    result = await create_webhook(req, _request("ws-a"))

    assert result["status"] == "success"
    assert result["webhook"]["url"] == "http://example.com/hook"
    assert len(call_threads) == 1
    # The whole point of asyncio.to_thread: register() must NOT run on the
    # event loop's own thread.
    assert call_threads[0] is not main_thread


@pytest.mark.asyncio
async def test_create_webhook_rejects_bad_scheme():
    req = WebhookCreateRequest(url="ftp://example.com/hook", events=["*"])
    with pytest.raises(Exception):
        await create_webhook(req, _request("ws-a"))


@pytest.mark.asyncio
async def test_update_webhook_offloads_update_to_worker_thread(monkeypatch):
    main_thread = threading.current_thread()
    call_threads: list[threading.Thread] = []

    def fake_update(sub_id, workspace_id, **fields) -> WebhookSubscription:
        # Stands in for the real update()->_save() blocking disk write:
        # records which thread executed it.
        call_threads.append(threading.current_thread())
        return WebhookSubscription(
            id=sub_id,
            url=fields.get("url", "http://example.com/hook"),
            events=fields.get("events", ["*"]),
            secret=fields.get("secret"),
            headers=fields.get("headers") or {},
            retries=fields.get("retries", 3),
            description=fields.get("description", ""),
            workspace_id=workspace_id,
        )

    monkeypatch.setattr(webhook_dispatcher, "update", fake_update)

    req = WebhookUpdateRequest(url="http://example.com/updated")
    result = await update_webhook("sub-1", req, _request("ws-a"))

    assert result["status"] == "success"
    assert result["webhook"]["url"] == "http://example.com/updated"
    assert len(call_threads) == 1
    # The whole point of asyncio.to_thread: update() must NOT run on the
    # event loop's own thread.
    assert call_threads[0] is not main_thread


@pytest.mark.asyncio
async def test_update_webhook_returns_404_for_missing_sub(monkeypatch):
    monkeypatch.setattr(webhook_dispatcher, "update", lambda *a, **k: None)

    req = WebhookUpdateRequest(url="http://example.com/updated")
    with pytest.raises(Exception):
        await update_webhook("nonexistent", req, _request("ws-a"))
