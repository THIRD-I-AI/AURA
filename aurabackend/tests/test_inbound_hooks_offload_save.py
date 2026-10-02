"""BUG-242: every inbound-hook registry mutator rewrites the whole JSON store with a
blocking open() + json.dump(). They ran directly inside async handlers -- including the
PUBLIC /hooks/fire/{slug} -- so each call stalled the single uvicorn worker.

Same proof as tests/test_webhooks_router_bug_async_save.py: the registry's synchronous
methods must run on a worker thread, not on the thread running the handler.
"""
from __future__ import annotations

import os
import sys
import threading

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.inbound_hooks as ih
from api_gateway.routers import inbound_hooks as router_mod


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(ih, "_STORE_PATH", str(tmp_path / "inbound.json"))
    monkeypatch.setattr(ih, "_DATA_DIR", str(tmp_path))
    reg = ih.InboundHookRegistry()
    monkeypatch.setattr(router_mod, "inbound_hooks", reg)
    monkeypatch.setattr(router_mod, "current_workspace_id", lambda request: "orgA")
    return reg


def _request(body: bytes = b"") -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/x", "headers": [], "query_string": b""}, receive)


def _record_save_threads(reg, monkeypatch) -> list:
    threads: list = []
    real_save = reg._save

    def _save():
        threads.append(threading.current_thread())
        real_save()

    monkeypatch.setattr(reg, "_save", _save)
    return threads


@pytest.mark.asyncio
async def test_create_update_delete_save_off_the_handler_thread(registry, monkeypatch):
    saves = _record_save_threads(registry, monkeypatch)
    here = threading.current_thread()

    created = await router_mod.create_hook(
        router_mod.HookCreateRequest(slug="s1", kind="agent", target="a1"), _request())
    hook_id = created["hook"]["id"]
    await router_mod.update_hook(hook_id, router_mod.HookUpdateRequest(description="d"), _request())
    await router_mod.delete_hook(hook_id, _request())

    assert len(saves) == 3
    assert all(t is not here for t in saves), "the store was rewritten on the event-loop thread"


@pytest.mark.asyncio
async def test_the_public_fire_endpoint_saves_off_the_handler_thread(registry, monkeypatch):
    registry.register(workspace_id="orgA", slug="pub", kind="agent", target="a1")
    saves = _record_save_threads(registry, monkeypatch)
    here = threading.current_thread()

    async def _no_agent(*args, **kwargs):
        return {"status": "ok"}

    monkeypatch.setattr(router_mod, "_fire_agent", _no_agent)
    await router_mod.fire_hook("pub", _request(b"{}"))

    assert len(saves) == 1 and saves[0] is not here
    assert registry.by_slug("pub").fire_count == 1


def test_concurrent_mutations_do_not_corrupt_the_store(registry):
    """The mutators now run on worker threads, so they must be safe to run together."""
    errors: list = []

    def _churn(i: int) -> None:
        try:
            for j in range(15):
                hook = registry.register(workspace_id="orgA", slug=f"s{i}-{j}", kind="agent", target="a")
                registry.record_fire(hook)
                registry.delete(hook.id, "orgA")
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    workers = [threading.Thread(target=_churn, args=(i,)) for i in range(6)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert errors == []
    assert registry.list("orgA") == []
