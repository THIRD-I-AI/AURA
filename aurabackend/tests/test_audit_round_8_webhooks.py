"""Audit round 8 -- webhooks area regressions (BUG-495, BUG-496)."""
from __future__ import annotations

import asyncio
import glob
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.inbound_hooks as ih
import shared.webhook_dispatcher as wd
from shared.streaming_manager import StreamEvent

# ── BUG-495 ────────────────────────────────────────────────────────

@pytest.fixture
def dispatcher(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(wd, "_STORE_PATH", str(tmp_path / "subscriptions.json"))
    return wd.WebhookDispatcher()


class _RegisteringSub(wd.WebhookSubscription):
    """Registers a new subscription from another thread while it is being matched --
    what POST /webhooks (asyncio.to_thread) does while the event loop is in _targets."""

    dispatcher: "wd.WebhookDispatcher" = None  # type: ignore[assignment]

    def matches(self, event_type: str) -> bool:
        if self.dispatcher is not None:
            d, self.dispatcher = self.dispatcher, None
            t = threading.Thread(target=d.register, args=("ws1", "http://new", ["*"]))
            t.start()
            t.join(timeout=5)
            assert not t.is_alive(), "register() blocked behind _targets"
        return super().matches(event_type)


def test_bug_495_targets_survives_concurrent_register(dispatcher):
    sub = _RegisteringSub(id="a", url="http://a", events=["*"], workspace_id="ws1")
    sub.dispatcher = dispatcher
    dispatcher._subs[sub.id] = sub
    dispatcher._subs["b"] = wd.WebhookSubscription(id="b", url="http://b", events=["*"], workspace_id="ws1")

    event = StreamEvent(topic="pipeline:1", event_type="complete", payload={}, workspace_id="ws1")
    # Old code: RuntimeError: dictionary changed size during iteration.
    matched = dispatcher._targets("pipeline.complete", event)
    assert {s.id for s in matched} >= {"a", "b"}
    assert len(dispatcher._subs) == 3


def test_bug_495_run_loop_survives_a_failing_event(dispatcher, monkeypatch):
    delivered = []

    async def fake_deliver(sub, event_type, payload):
        delivered.append(payload["topic"])

    monkeypatch.setattr(dispatcher, "_deliver", fake_deliver)
    dispatcher._subs["s"] = wd.WebhookSubscription(id="s", url="http://s", events=["*"], workspace_id="ws1")

    real_targets = dispatcher._targets
    calls = {"n": 0}

    def flaky_targets(event_type, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dictionary changed size during iteration")
        return real_targets(event_type, event)

    monkeypatch.setattr(dispatcher, "_targets", flaky_targets)

    async def scenario():
        dispatcher._queue = asyncio.Queue()
        dispatcher._stop.clear()
        task = asyncio.create_task(dispatcher._run())
        await dispatcher._queue.put(StreamEvent(topic="pipeline:bad", event_type="complete", payload={}, workspace_id="ws1"))
        await dispatcher._queue.put(StreamEvent(topic="pipeline:good", event_type="complete", payload={}, workspace_id="ws1"))
        for _ in range(100):
            if delivered or task.done():
                break
            await asyncio.sleep(0.01)
        alive = not task.done()
        dispatcher._stop.set()
        try:
            await asyncio.wait_for(task, timeout=3)
        except Exception:
            pass
        return alive

    alive = asyncio.run(scenario())
    assert alive, "dispatcher task died on one bad event"
    assert delivered == ["pipeline:good"]


# ── BUG-496 ────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "inbound.json"
    monkeypatch.setattr(ih, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ih, "_STORE_PATH", str(path))
    return path


def test_bug_496_failed_write_keeps_previous_store(store, monkeypatch):
    reg = ih.InboundHookRegistry()
    a = reg.register("ws1", "slug-a", "agent", "prompt a")
    reg.register("ws2", "slug-b", "agent", "prompt b")

    real_dump = json.dump

    def dump_then_die(obj, f, **kw):
        f.write('[{"id": "trunc')  # partial JSON, then the write fails (ENOSPC, kill)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ih.json, "dump", dump_then_die)
    reg.record_fire(a)
    monkeypatch.setattr(ih.json, "dump", real_dump)

    reloaded = ih.InboundHookRegistry()
    assert reloaded.by_slug("slug-a") is not None
    assert reloaded.by_slug("slug-b") is not None
    assert glob.glob(str(store) + ".*.tmp") == []


def test_bug_496_unreadable_store_is_not_overwritten(store):
    corrupt = '[{"id": "x", "slug": "keep-me", "kind": "agent", "targ'
    store.write_text(corrupt, encoding="utf-8")

    reg = ih.InboundHookRegistry()
    reg.register("ws1", "new-slug", "agent", "p")

    # The unreadable original must survive somewhere so the hooks can be recovered.
    preserved = [p for p in glob.glob(str(store) + "*") if p != str(store)]
    contents = [open(p, encoding="utf-8").read() for p in preserved]
    assert corrupt in contents
