"""BUG-235: POST /chat/history took an unvalidated dict.

Any JSON value was stored as ``metadata``; a list or string there made every later
GET /chat/history for that session fail validation -> a permanent 500. The client
also chose the row's primary key.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from pydantic import ValidationError
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway import persistence
from api_gateway.routers import chat as chatmod


@pytest.fixture(autouse=True)
def _fresh_engine():
    # Same per-test engine rebind as tests/test_dashboards_persistence.py.
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    yield


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/x"})


def test_non_object_metadata_is_rejected_at_the_boundary():
    for bad in (["a", "b"], "text", 7):
        with pytest.raises(ValidationError):
            chatmod.ChatMessageSave(type="user", content="hi", metadata=bad)
    assert chatmod.ChatMessageSave(content="hi", metadata={"k": 1}).metadata == {"k": 1}


def test_type_and_content_are_bounded():
    with pytest.raises(ValidationError):
        chatmod.ChatMessageSave(type="x" * 33, content="hi")
    with pytest.raises(ValidationError):
        chatmod.ChatMessageSave(content="x" * 200_001)


@pytest.mark.asyncio
async def test_id_is_server_generated_so_a_repeated_client_id_cannot_collide(monkeypatch):
    ws = f"ws-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(chatmod, "current_workspace_id", lambda req: ws)
    session = f"s-{uuid.uuid4().hex[:8]}"
    # `id` is not a field any more: a client-sent one is ignored, not used as the key.
    payload = chatmod.ChatMessageSave.model_validate({"id": "same", "type": "user", "content": "one"})
    first = await chatmod.save_chat_message(session, payload, _request())
    second = await chatmod.save_chat_message(session, payload, _request())
    assert first.id != second.id and "same" not in (first.id, second.id)
    assert len(await chatmod.get_chat_history(session, _request())) == 2


@pytest.mark.asyncio
async def test_a_legacy_row_with_non_object_metadata_does_not_break_the_session(monkeypatch):
    ws = f"ws-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(chatmod, "current_workspace_id", lambda req: ws)
    session = f"s-{uuid.uuid4().hex[:8]}"
    # What the old endpoint could store.
    await persistence.insert_chat_message(ws, {"session_id": session, "type": "user", "content": "bad", "metadata": ["x"]})
    await persistence.insert_chat_message(ws, {"session_id": session, "type": "user", "content": "good", "metadata": {"k": 1}})

    history = await chatmod.get_chat_history(session, _request())

    by_content = {m.content: m.metadata for m in history}
    assert by_content == {"bad": None, "good": {"k": 1}}
