"""Task 4 — POST /chat/stream SSE endpoint + blocking->async bridge.

Hermetic: the heavy bits (async schema build + LLM provider) are patched, so
the test exercises the flag gate, the worker-thread -> asyncio.Queue bridge,
and the SSE framing without a network or a real model."""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app(monkeypatch, enabled: bool):
    monkeypatch.setenv("AURA_COMMANDER_ENABLED", "true" if enabled else "false")
    import shared.config as cfg
    importlib.reload(cfg)
    import api_gateway.routers.chat as chat
    importlib.reload(chat)
    app = FastAPI()
    app.include_router(chat.router)
    return app, chat


def test_stream_404_when_flag_off(monkeypatch):
    app, _ = _app(monkeypatch, enabled=False)
    client = TestClient(app)
    r = client.post("/chat/stream", json={"message": "hi"})
    assert r.status_code == 404


def test_stream_emits_events_when_enabled(monkeypatch):
    app, chat = _app(monkeypatch, enabled=True)

    async def _fake_session(http_request, req):
        import duckdb
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE sales (amount INTEGER)")
        con.execute("INSERT INTO sales VALUES (10), (20)")
        return con, "TABLE sales(amount)", "tenant1"

    from shared.llm_provider import AssistantTurn, ToolCall

    class FakeLLM:
        model = "fake"

        def __init__(self):
            self._n = 0

        def complete_with_tools(self, messages, tools, **kw):
            self._n += 1
            if self._n == 1:
                return AssistantTurn(None, [ToolCall("c1", "run_sql", {"sql": "SELECT amount FROM sales"})], "tool_calls")
            return AssistantTurn("Two rows.", [], "stop")

    monkeypatch.setattr(chat, "_build_commander_session", _fake_session)
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: FakeLLM())

    client = TestClient(app)
    with client.stream("POST", "/chat/stream", json={"message": "show sales"}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(chunk for chunk in r.iter_text())

    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "event: text" in body
    assert "event: done" in body


def test_stream_400_on_empty_message(monkeypatch):
    app, _ = _app(monkeypatch, enabled=True)
    client = TestClient(app)
    r = client.post("/chat/stream", json={"message": "   "})
    assert r.status_code == 400
