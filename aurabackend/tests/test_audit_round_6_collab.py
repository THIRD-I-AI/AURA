"""Audit round 6 regressions for the collab WebSocket relay."""

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import collab as collab_module
from api_gateway.routers.collab import router as collab_router
from shared.config import settings


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(collab_router)
    return TestClient(app)


@pytest.fixture
def api_key_only(monkeypatch):
    # BUG-456: the documented opt-in gate -- AURA_API_KEY set, JWT left off.
    monkeypatch.setattr(settings, "api_key", "secret")
    monkeypatch.setattr(settings, "jwt_enabled", False)
    yield _client()
    collab_module._rooms.clear()


def test_bug456_ws_without_api_key_is_rejected(api_key_only):
    # Old code accepted the handshake, so websocket_connect returned a live socket.
    with pytest.raises(WebSocketDisconnect) as exc:
        with api_key_only.websocket_connect("/ws/collab/room-x"):
            pass
    assert exc.value.code == 1008
    assert collab_module._rooms == {}


def test_bug456_ws_with_wrong_api_key_is_rejected(api_key_only):
    with pytest.raises(WebSocketDisconnect):
        with api_key_only.websocket_connect("/ws/collab/room-x?api_key=nope"):
            pass
    assert collab_module._rooms == {}


def test_bug456_ws_with_api_key_header_or_query_is_relayed(api_key_only):
    with api_key_only.websocket_connect(
        "/ws/collab/room-y", headers={"X-API-Key": "secret"}
    ) as a:
        with api_key_only.websocket_connect("/ws/collab/room-y?api_key=secret") as b:
            a.send_bytes(b"\x00\x01")
            assert b.receive_bytes() == b"\x00\x01"


def test_bug456_no_api_key_configured_keeps_open_mode(monkeypatch):
    monkeypatch.setattr(settings, "api_key", None)
    monkeypatch.setattr(settings, "jwt_enabled", False)
    client = _client()
    try:
        with client.websocket_connect("/ws/collab/room-z") as a:
            with client.websocket_connect("/ws/collab/room-z") as b:
                a.send_text("hi")
                assert b.receive_text() == "hi"
    finally:
        collab_module._rooms.clear()
