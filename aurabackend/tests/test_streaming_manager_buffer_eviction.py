"""BUG-314: the event bus kept a replay buffer for every topic ever published to --
one per query, agent run, upload and pipeline run -- for the life of the process."""
from __future__ import annotations

import asyncio

from shared.streaming_manager import StreamEvent, StreamingManager


def _publish_topics(manager: StreamingManager, topics) -> None:
    async def run():
        for t in topics:
            await manager.publish(StreamEvent(topic=t, event_type="progress", payload={"n": 1}))

    asyncio.run(run())


def test_the_number_of_buffered_topics_is_capped(monkeypatch):
    monkeypatch.setattr(StreamingManager, "_MAX_TOPICS", 10)
    manager = StreamingManager()

    _publish_topics(manager, [f"query:{i}" for i in range(250)])

    assert len(manager._buffers) == 10
    assert list(manager._buffers) == [f"query:{i}" for i in range(240, 250)]


def test_a_topic_still_being_published_to_is_not_the_one_dropped(monkeypatch):
    monkeypatch.setattr(StreamingManager, "_MAX_TOPICS", 3)
    manager = StreamingManager()

    _publish_topics(manager, ["long-job", "a", "b", "long-job", "c", "d"])

    assert "long-job" in manager._buffers
    assert len(manager._buffers["long-job"]) == 2
    assert "a" not in manager._buffers


def test_per_topic_history_is_still_bounded_and_ordered():
    manager = StreamingManager()

    async def run():
        for i in range(120):
            await manager.publish(StreamEvent(topic="t", event_type="progress", payload={"i": i}))

    asyncio.run(run())

    kept = [e.payload["i"] for e in manager._buffers["t"]]
    assert kept == list(range(70, 120))
