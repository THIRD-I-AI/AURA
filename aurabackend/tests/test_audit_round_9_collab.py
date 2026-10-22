"""Audit round 9 regressions for the collab WebSocket relay."""

import os
import sys
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import collab as collab_module
from api_gateway.routers.collab import router as collab_router
from shared.auth import create_access_token
from shared.config import settings


class _Clock:
    """Stands in for the ``time`` module inside collab only, so the handshake
    decode (PyJWT, real clock) is unaffected while the relay sees time move."""

    def __init__(self) -> None:
        self.offset = 0.0

    def time(self) -> float:
        return time.time() + self.offset


@pytest.fixture
def jwt_client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", None)
    monkeypatch.setattr(settings, "jwt_enabled", True)
    clock = _Clock()
    monkeypatch.setattr(collab_module, "time", SimpleNamespace(time=clock.time), raising=False)
    app = FastAPI()
    app.include_router(collab_router)
    yield TestClient(app), clock
    collab_module._rooms.clear()


def _token(sub: str, **extra) -> str:
    return create_access_token({"sub": sub, "org_id": "orgO", **extra})


def test_bug519_expired_socket_stops_receiving_peer_frames(jwt_client):
    # Old code: A's 30-minute token ran out an hour ago, yet B's frame was still
    # delivered to A. Now A is closed with 1008 instead.
    client, clock = jwt_client
    tok_b = create_access_token({"sub": "b", "org_id": "orgO"}, expires_delta=timedelta(hours=2))
    with client.websocket_connect(f"/ws/collab/q3?token={_token('a')}") as a:
        with client.websocket_connect(f"/ws/collab/q3?token={tok_b}") as b:
            clock.offset = 3600
            b.send_text("secret-update")
            msg = a.receive()
            assert msg["type"] == "websocket.close", msg
            assert msg["code"] == 1008


def test_bug519_frames_from_expired_socket_are_not_relayed(jwt_client):
    client, clock = jwt_client
    long = timedelta(hours=2)
    stolen = _token("a")  # default 30-minute token
    tok_b = create_access_token({"sub": "b", "org_id": "orgO"}, expires_delta=long)
    tok_c = create_access_token({"sub": "c", "org_id": "orgO"}, expires_delta=long)
    with client.websocket_connect(f"/ws/collab/q3?token={stolen}") as a:
        with client.websocket_connect(f"/ws/collab/q3?token={tok_b}") as b:
            with client.websocket_connect(f"/ws/collab/q3?token={tok_c}") as c:
                clock.offset = 3600
                a.send_bytes(b"evil-yjs-update")
                c.send_text("ok")
                # Old code: A was never closed and simply got C's "ok".
                msg = a.receive()
                assert msg["type"] == "websocket.close", msg
                assert msg["code"] == 1008
                # A's frame never reached B; the first thing B sees is C's.
                assert b.receive_text() == "ok"


def test_bug519_session_cap_applies_even_with_unexpired_token(jwt_client):
    # A refreshed token keeps its original auth_time; 13h after sign-in the
    # socket must die even though this token's own exp has not passed.
    client, clock = jwt_client
    long = timedelta(hours=24)
    old_session = create_access_token(
        {"sub": "a", "org_id": "orgO", "auth_time": time.time() - 11 * 3600}, expires_delta=long
    )
    tok_b = create_access_token({"sub": "b", "org_id": "orgO"}, expires_delta=long)
    with client.websocket_connect(f"/ws/collab/q3?token={old_session}") as a:
        with client.websocket_connect(f"/ws/collab/q3?token={tok_b}") as b:
            clock.offset = 2 * 3600  # A is now 13h into its session
            b.send_text("secret-update")
            msg = a.receive()
            assert msg["type"] == "websocket.close", msg
            assert msg["code"] == 1008


def test_bug519_live_tokens_still_relay(jwt_client):
    client, _ = jwt_client
    with client.websocket_connect(f"/ws/collab/q3?token={_token('a')}") as a:
        with client.websocket_connect(f"/ws/collab/q3?token={_token('b')}") as b:
            a.send_bytes(b"\x00\x01")
            assert b.receive_bytes() == b"\x00\x01"
            b.send_text("hi")
            assert a.receive_text() == "hi"


def test_bug519_a_stalled_close_does_not_hold_up_the_sender(monkeypatch):
    # Review: expired and dropped peers were closed one at a time with no timeout,
    # so a peer stalled in its close handshake stalled the sender (BUG-372 again).
    import asyncio
    import time as _time
    import types

    from api_gateway.routers import collab

    monkeypatch.setenv("AURA_COLLAB_SEND_TIMEOUT", "0.2")

    class _Peer:
        def __init__(self, deadline=None):
            self.got = []
            self.state = types.SimpleNamespace(collab_deadline=deadline)

        async def send_bytes(self, data):
            self.got.append(data)

        async def close(self, code: int = 1000):
            await asyncio.sleep(30)

    async def _scenario():
        room = collab._Room()
        sender, expired, live = _Peer(), _Peer(deadline=_time.time() - 1), _Peer()
        room.clients.update({sender, expired, live})
        started = _time.monotonic()
        await asyncio.wait_for(collab._broadcast(room, sender, b"hi"), timeout=5)
        return _time.monotonic() - started, room, expired, live

    elapsed, room, expired, live = asyncio.run(_scenario())
    assert elapsed < 2
    assert live.got == [b"hi"] and expired.got == []
    assert expired not in room.clients
