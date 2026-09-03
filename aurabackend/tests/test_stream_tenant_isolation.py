"""
Stream Tenant Isolation Tests (BUG-041)
=========================================
The universal SSE bus (`shared/streaming_manager.py`) fanned out every event
to every subscriber with zero tenant scoping -- including the documented
`/stream/*` and `/stream/monitor:*` wildcards, which matched (and leaked)
every other tenant's live query results, ETL/pipeline progress, and agent
output to any authenticated caller.

Covers:
  1. Exact-topic isolation: two subscribers on the same topic, different
     workspace_id, only the matching one receives the event.
  2. The wildcard-leak regression -- the direct proof for this bug: a `"*"`
     subscriber for tenant B must not receive tenant A's event.
  3. Untenanted events (e.g. `system:health`) stay visible to everyone --
     the deliberate "leave global" behaviour must not regress.
  4. `get_buffered_events` (Last-Event-ID / `replay=true`) is filtered the
     same way, so replay can't leak buffered cross-tenant events either.
  5. Router-level end-to-end: a real `GET /stream/{topic}` SSE connection,
     authenticated via `JWTAuthMiddleware` with a tenant-A JWT, never
     receives a live event published for tenant B on the same topic.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from starlette.requests import Request as StarletteRequest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.stream import stream_topic
from shared.auth import create_access_token, decode_access_token
from shared.streaming_manager import StreamEvent, StreamingManager, streaming_manager


@pytest.fixture(autouse=True)
def _clean_bus():
    """The streaming bus is a process-wide singleton shared with every other
    test module in the suite -- never leak subscribers/buffers across tests."""
    streaming_manager._subscribers.clear()
    streaming_manager._buffers.clear()
    yield
    streaming_manager._subscribers.clear()
    streaming_manager._buffers.clear()


def _topic() -> str:
    return f"query:job-{uuid.uuid4().hex[:8]}"


# ── 1. Exact-topic isolation ─────────────────────────────────────────────

async def test_same_topic_different_workspace_only_matching_subscriber_gets_it():
    topic = _topic()
    sub_a_id, queue_a = streaming_manager.subscribe(topic, "tenant-a")
    sub_b_id, queue_b = streaming_manager.subscribe(topic, "tenant-b")

    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="complete",
        payload={"result": "tenant-a-data"}, workspace_id="tenant-a",
    ))

    assert queue_a.get_nowait().payload["result"] == "tenant-a-data"
    assert queue_b.empty()

    streaming_manager.unsubscribe(sub_a_id)
    streaming_manager.unsubscribe(sub_b_id)


# ── 2. Wildcard-leak regression -- the exact scenario from the finding ──

async def test_wildcard_subscriber_does_not_receive_other_tenants_event():
    """This is the direct proof for the bug: `/stream/*` (and `monitor:*`)
    topic-match everything, so before the fix a wildcard subscriber for one
    tenant received every other tenant's events regardless of topic."""
    topic = _topic()
    sub_id, queue = streaming_manager.subscribe("*", "tenant-b")

    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="complete",
        payload={"secret": "tenant-a-only"}, workspace_id="tenant-a",
    ))

    assert queue.empty(), "tenant-b's wildcard subscriber received tenant-a's event"
    streaming_manager.unsubscribe(sub_id)


async def test_monitor_wildcard_subscriber_does_not_receive_other_tenants_event():
    sub_id, queue = streaming_manager.subscribe("monitor:*", "tenant-b")

    await streaming_manager.publish(StreamEvent(
        topic="monitor:alert", event_type="data",
        payload={"secret": "tenant-a-only"}, workspace_id="tenant-a",
    ))

    assert queue.empty()
    streaming_manager.unsubscribe(sub_id)


# ── 3. Untenanted events stay globally visible (system:health etc.) ─────

async def test_untenanted_event_reaches_every_subscriber_regardless_of_tenant():
    topic = _topic()
    _id_a, queue_a = streaming_manager.subscribe(topic, "tenant-a")
    _id_b, queue_b = streaming_manager.subscribe(topic, "tenant-b")
    _id_open, queue_open = streaming_manager.subscribe(topic, None)

    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="data",
        payload={"kind": "system-health"}, workspace_id=None,
    ))

    assert queue_a.get_nowait().payload["kind"] == "system-health"
    assert queue_b.get_nowait().payload["kind"] == "system-health"
    assert queue_open.get_nowait().payload["kind"] == "system-health"


async def test_unauthenticated_dev_mode_subscriber_sees_tenanted_event():
    """Same fail-open-only-in-open-mode contract as current_workspace_id():
    a subscriber with no resolvable workspace_id (open/dev mode) still sees
    tenanted events -- it just can't be used to isolate anything itself."""
    topic = _topic()
    _id, queue = streaming_manager.subscribe(topic, None)

    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="data",
        payload={"kind": "tenant-a-event"}, workspace_id="tenant-a",
    ))

    assert queue.get_nowait().payload["kind"] == "tenant-a-event"


# ── 4. get_buffered_events replay isolation ──────────────────────────────

async def test_get_buffered_events_filters_by_workspace():
    topic = _topic()
    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="data", payload={"who": "a1"}, workspace_id="tenant-a",
    ))
    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="data", payload={"who": "b1"}, workspace_id="tenant-b",
    ))
    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="data", payload={"who": "a2"}, workspace_id="tenant-a",
    ))

    tenant_a_view = streaming_manager.get_buffered_events(topic, workspace_id="tenant-a")
    assert [ev.payload["who"] for ev in tenant_a_view] == ["a1", "a2"]

    tenant_b_view = streaming_manager.get_buffered_events(topic, workspace_id="tenant-b")
    assert [ev.payload["who"] for ev in tenant_b_view] == ["b1"]


async def test_get_buffered_events_untenanted_caller_sees_everything():
    """Matches current_workspace_id()'s unauthenticated fallback: an open-mode
    caller (workspace_id=None) is not itself a tenant boundary."""
    topic = _topic()
    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="data", payload={"who": "a"}, workspace_id="tenant-a",
    ))
    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="data", payload={"who": "system"}, workspace_id=None,
    ))

    everything = streaming_manager.get_buffered_events(topic, workspace_id=None)
    assert [ev.payload["who"] for ev in everything] == ["a", "system"]


def test_workspace_allowed_matrix():
    allowed = StreamingManager._workspace_allowed
    assert allowed(None, None) is True
    assert allowed(None, "tenant-a") is True
    assert allowed("tenant-a", None) is True
    assert allowed("tenant-a", "tenant-a") is True
    assert allowed("tenant-a", "tenant-b") is False


# ── 5. Router-level end-to-end through stream.py's real route coroutine ──
#
# `stream_topic`'s SSE connection is intentionally infinite (heartbeats
# every 20s until the client disconnects), which httpx's ASGITransport
# cannot drive concurrently with a producer publishing mid-connection --
# it runs the whole ASGI app to completion before handing back any bytes,
# so a never-terminating StreamingResponse simply hangs forever under it
# (confirmed: even a bare, middleware-free router deadlocks the same way).
# Real deployments don't hit this -- uvicorn streams incrementally -- it is
# an ASGITransport/TestClient testing-harness limitation, not a product bug.
#
# So these tests call `stream_topic()` -- the actual, undecorated route
# coroutine -- directly and drive the real `StreamingResponse.body_iterator`
# it returns (the genuine `_gen()` / `_event_generator()` production code),
# with a hand-built `Request` whose `state.user` is set via the exact same
# `shared.auth.decode_access_token` call `JWTAuthMiddleware.dispatch` uses
# (that middleware's own token-handling is already covered by
# `test_middleware.py`) -- a real, live SSE connection and a real live
# publish, just without the ASGI transport layer that can't support it.

class _NeverDisconnects:
    """Mimics a live, still-connected ASGI receive channel. It blocks
    (yields control) rather than resolving, so Request.is_disconnected()'s
    already-cancelled-CancelScope probe reports "not disconnected" -- the
    same observable behaviour a real, still-open connection produces."""

    async def __call__(self):
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}  # pragma: no cover -- never reached


def _authed_request(path: str, token: str, query_string: bytes = b"") -> StarletteRequest:
    scope = {
        "type": "http", "method": "GET", "path": path,
        "raw_path": path.encode(), "query_string": query_string,
        "headers": [], "client": ("test", 12345), "server": ("test", 80),
        "scheme": "http", "http_version": "1.1",
    }
    request = StarletteRequest(scope, receive=_NeverDisconnects())
    request.state.user = decode_access_token(token)
    return request


async def test_router_sse_stream_never_delivers_another_tenants_live_event():
    workspace_a = f"tenant-a-{uuid.uuid4().hex[:6]}"
    workspace_b = f"tenant-b-{uuid.uuid4().hex[:6]}"
    topic = _topic()
    token_a = create_access_token({"sub": "user-a", "org_id": workspace_a})
    request = _authed_request(f"/stream/{topic}", token_a)

    resp = await stream_topic(topic=topic, request=request, last_event_id=None, replay=False)
    assert resp.media_type == "text/event-stream"
    agen = resp.body_iterator

    try:
        next_task = asyncio.ensure_future(agen.__anext__())

        for _ in range(200):
            if streaming_manager.subscriber_count(topic) >= 1:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("SSE subscription never registered")

        # Tenant B's event on the SAME topic -- must never arrive.
        await streaming_manager.publish(StreamEvent(
            topic=topic, event_type="data",
            payload={"marker": "tenant-b-secret"}, workspace_id=workspace_b,
        ))
        await asyncio.sleep(0.1)
        assert not next_task.done(), "tenant B's event reached tenant A's live SSE connection"

        # Tenant A's own event -- proves the connection is genuinely live
        # and not just silently dropping everything.
        await streaming_manager.publish(StreamEvent(
            topic=topic, event_type="data",
            payload={"marker": "tenant-a-own"}, workspace_id=workspace_a,
        ))
        chunk = await asyncio.wait_for(next_task, timeout=5.0)
        assert "tenant-a-own" in chunk
        assert "tenant-b-secret" not in chunk
    finally:
        await agen.aclose()


async def test_router_sse_replay_excludes_another_tenants_buffered_event():
    """The `replay=true` bulk-replay path (and Last-Event-ID replay) must
    filter buffered events the same way live fanout does."""
    workspace_a = f"tenant-a-{uuid.uuid4().hex[:6]}"
    workspace_b = f"tenant-b-{uuid.uuid4().hex[:6]}"
    topic = _topic()

    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="complete",
        payload={"marker": "tenant-b-buffered-secret"}, workspace_id=workspace_b,
    ))
    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="complete",
        payload={"marker": "tenant-a-buffered-own"}, workspace_id=workspace_a,
    ))

    token_a = create_access_token({"sub": "user-a", "org_id": workspace_a})
    request = _authed_request(f"/stream/{topic}", token_a, query_string=b"replay=true")

    resp = await stream_topic(topic=topic, request=request, last_event_id=None, replay=True)
    agen = resp.body_iterator
    try:
        # replay=true yields every buffered event for this exact topic
        # before ever reaching the live loop -- collect exactly one chunk,
        # which is all a single-tenant buffer here produces.
        chunk = await asyncio.wait_for(agen.__anext__(), timeout=5.0)
        assert "tenant-a-buffered-own" in chunk
        assert "tenant-b-buffered-secret" not in chunk
    finally:
        await agen.aclose()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
