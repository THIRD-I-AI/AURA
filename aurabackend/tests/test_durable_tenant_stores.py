"""
S50 — contract tests for durable, tenant-scoped chat + pipeline persistence.

Mirrors test_api_gateway_persistence.py: a fresh per-test SQLite file via the
``gateway_db`` fixture, pure-Python (SQLite) so it runs on the base backend lane.

Coverage:
  * chat: durability + tenant isolation + per-session cap + session listing
  * pipelines: durability + tenant isolation + scoped/unscoped get + upsert
  * endpoint scoping: chat history + pipeline CRUD honour the caller's tenant
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from api_gateway import persistence as p


@pytest_asyncio.fixture
async def gateway_db(tmp_path, monkeypatch):
    """Fresh SQLite-backed gateway persistence per test."""
    db_path = tmp_path / f"gw_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    p._engine = None
    p._session_factory = None
    p._schema_initialized = False
    await p.init_database()
    yield
    await p.close_database()


def _msg(session_id: str, content: str, mtype: str = "user") -> dict:
    return {
        "id": None, "session_id": session_id, "type": mtype, "content": content,
        "timestamp": "2026-06-22T00:00:00", "metadata": {"k": 1},
    }


def _pipe(pid: str, ws: str, name: str = "P") -> dict:
    return {
        "id": pid, "workspace_id": ws, "name": name, "description": "d",
        "definition": {"id": pid, "name": name, "steps": [{"x": 1}]},
        "status": "draft", "source_label": "file:a.csv", "sink_type": "table",
        "step_count": 1, "tags": ["t"], "created_at": "2026-06-22T00:00:00",
        "updated_at": None,
    }


# ── Chat ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_durability_and_isolation(gateway_db) -> None:
    await p.insert_chat_message("orgA", _msg("s1", "hi A"))
    await p.insert_chat_message("orgB", _msg("s1", "hi B"))
    a = await p.list_chat_messages("orgA", "s1")
    assert [m["content"] for m in a] == ["hi A"]
    assert a[0]["metadata"] == {"k": 1}
    b = await p.list_chat_messages("orgB", "s1")
    assert [m["content"] for m in b] == ["hi B"]
    sessions = await p.list_chat_sessions("orgA")
    assert sessions[0]["session_id"] == "s1"
    assert sessions[0]["message_count"] == 1


@pytest.mark.asyncio
async def test_chat_session_cap(gateway_db) -> None:
    for i in range(105):
        await p.insert_chat_message("orgA", _msg("s1", f"m{i}"))
    rows = await p.list_chat_messages("orgA", "s1")
    assert len(rows) == p.CHAT_MESSAGES_PER_SESSION_CAP
    assert rows[-1]["content"] == "m104"  # newest kept, oldest evicted


# ── Pipelines ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_durability_and_isolation(gateway_db) -> None:
    await p.save_pipeline(_pipe("p1", "orgA", "A-pipe"))
    await p.save_pipeline(_pipe("p2", "orgB", "B-pipe"))
    la = await p.list_pipelines("orgA")
    assert [x["name"] for x in la] == ["A-pipe"]
    assert la[0]["source"] == "file:a.csv"
    assert la[0]["steps"] == 1
    assert la[0]["tags"] == ["t"]
    assert await p.list_pipelines("orgB")
    # B cannot read or delete A's pipeline
    assert await p.get_pipeline("p1", workspace_id="orgB") is None
    assert await p.delete_pipeline("p1", "orgB") is False
    # unscoped get (webhook path) returns the full definition
    full = await p.get_pipeline("p1")
    assert full["id"] == "p1"
    assert full["steps"] == [{"x": 1}]
    # scoped delete works for the owner
    assert await p.delete_pipeline("p1", "orgA") is True
    assert await p.get_pipeline("p1") is None


@pytest.mark.asyncio
async def test_pipeline_upsert_updates_in_place(gateway_db) -> None:
    await p.save_pipeline(_pipe("p1", "orgA", "v1"))
    await p.save_pipeline(_pipe("p1", "orgA", "v2"))
    rows = await p.list_pipelines("orgA")
    assert len(rows) == 1
    assert rows[0]["name"] == "v2"


# ── Endpoint scoping ─────────────────────────────────────────────────


def _bare_request():
    from fastapi import Request
    return Request({"type": "http", "headers": [], "method": "POST", "query_string": b""})


@pytest.mark.asyncio
async def test_chat_history_endpoint_scopes_by_tenant(gateway_db, monkeypatch) -> None:
    from api_gateway.routers import chat as chatmod
    seen = {"ws": "orgA"}
    monkeypatch.setattr(chatmod, "current_workspace_id", lambda req: seen["ws"])
    req = _bare_request()

    seen["ws"] = "orgA"
    await chatmod.save_chat_message("s1", {"type": "user", "content": "from A"}, req)

    seen["ws"] = "orgB"
    out_b = await chatmod.get_chat_history("s1", req)
    assert out_b == []  # B sees nothing of A's session

    seen["ws"] = "orgA"
    out_a = await chatmod.get_chat_history("s1", req)
    assert [m.content for m in out_a] == ["from A"]
