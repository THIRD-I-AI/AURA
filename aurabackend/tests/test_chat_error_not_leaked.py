"""BUG-240: the commander chat paths sent raw exception text to the client. A
SQLAlchemy error embeds the statement and its parameters; a storage error embeds
bucket and key paths. Only sanitize_error output may reach the response."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_gateway.routers.chat as chat

SECRET = "[SQL: INSERT INTO gateway_pipelines (id, workspace_id) VALUES ('p1', 'org-secret')]"


def test_stream_error_event_does_not_carry_the_raw_exception(monkeypatch):
    # Patch the flag on the router's own settings object. Reloading shared.config (as
    # test_commander_endpoint's helper does) swaps the settings object under every
    # module imported earlier and breaks whichever test file runs next.
    monkeypatch.setattr(chat.settings, "commander_enabled", True)
    app = FastAPI()
    app.include_router(chat.router)

    async def _fake_session(http_request, req):
        import duckdb
        return duckdb.connect(":memory:"), "", "tenant1"

    def _boom(*args, **kwargs):
        raise RuntimeError(SECRET)
        yield  # pragma: no cover  (makes this a generator, like run_commander)

    monkeypatch.setattr(chat, "_build_commander_session", _fake_session)
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: object())
    monkeypatch.setattr(chat, "run_commander", _boom)

    client = TestClient(app)
    with client.stream("POST", "/chat/stream", json={"message": "show sales"}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    assert "event: error" in body
    assert "org-secret" not in body and "INSERT INTO" not in body
    assert "Internal server error" in body


def test_no_chat_path_formats_the_exception_into_a_response():
    """The pipeline- and audit-intent handlers had the same leak; guard all three."""
    import inspect

    src = inspect.getsource(chat)
    assert "str(exc)[:200]" not in src
    assert "message=str(exc)" not in src
