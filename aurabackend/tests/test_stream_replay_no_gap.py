"""BUG-244: GET /stream/{topic}?replay=true sent the buffered events first and only
then subscribed. An event published while the replay was being sent was in neither the
replay nor the client's queue -- so the 'complete' of a short run could be lost and the
UI spinner never stopped."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import stream as stream_mod
from shared.streaming_manager import StreamEvent, streaming_manager


def _request() -> Request:
    async def receive():
        await asyncio.sleep(3600)  # never disconnects during the test

    return Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/x"}, receive)


async def _next(iterator) -> str:
    return await asyncio.wait_for(iterator.__anext__(), timeout=5)


@pytest.mark.asyncio
async def test_an_event_published_during_the_replay_is_not_lost(monkeypatch):
    # A short heartbeat makes the old behaviour fail fast (it yields a heartbeat
    # where the lost event should have been) instead of waiting 20 seconds.
    monkeypatch.setattr(stream_mod, "_HEARTBEAT_INTERVAL", 0.3)
    monkeypatch.setattr(stream_mod, "current_workspace_id", lambda request: "ws1")
    topic = f"etl:{uuid.uuid4().hex}"
    for i in range(2):
        await streaming_manager.publish(StreamEvent(
            topic=topic, event_type="progress", payload={"step": i}, workspace_id="ws1"))

    response = await stream_mod.stream_topic(topic, _request(), last_event_id=None, replay=True)
    chunks = response.body_iterator
    try:
        first = await _next(chunks)
        assert '"step": 0' in first or '"step":0' in first

        # The client is mid-replay (suspended on a yield) when the run finishes.
        await streaming_manager.publish(StreamEvent(
            topic=topic, event_type="complete", payload={"done": True}, workspace_id="ws1"))

        second = await _next(chunks)
        assert '"step": 1' in second or '"step":1' in second
        third = await _next(chunks)
        assert "complete" in third and "heartbeat" not in third, f"the 'complete' event was lost: {third!r}"
    finally:
        await chunks.aclose()


@pytest.mark.asyncio
async def test_replay_does_not_deliver_a_buffered_event_twice(monkeypatch):
    monkeypatch.setattr(stream_mod, "_HEARTBEAT_INTERVAL", 0.3)
    monkeypatch.setattr(stream_mod, "current_workspace_id", lambda request: "ws1")
    topic = f"etl:{uuid.uuid4().hex}"
    await streaming_manager.publish(StreamEvent(
        topic=topic, event_type="progress", payload={"step": 0}, workspace_id="ws1"))

    response = await stream_mod.stream_topic(topic, _request(), last_event_id=None, replay=True)
    chunks = response.body_iterator
    try:
        assert "progress" in await _next(chunks)
        assert "heartbeat" in await _next(chunks)  # nothing else queued: no duplicate
    finally:
        await chunks.aclose()
