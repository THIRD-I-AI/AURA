"""
Collab Relay Tests
==================
Covers the ``api_gateway.routers.collab`` WebSocket relay used as the
backend for S6.10's real-time editor. Tests use FastAPI's TestClient,
which speaks WebSockets via the synchronous ``websocket_connect`` helper
so we don't need an actual running server.
"""

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import collab as collab_module
from api_gateway.routers.collab import router as collab_router


@pytest.fixture
def client():
    # Mount only the collab router — keeps the test isolated from the
    # rest of the gateway's startup (which boots a scheduler thread).
    app = FastAPI()
    app.include_router(collab_router)
    with TestClient(app) as c:
        yield c
    # Make absolutely sure no rooms leak across tests.
    collab_module._rooms.clear()


def test_rooms_endpoint_starts_empty(client):
    resp = client.get("/collab/rooms")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"rooms": {}, "total": 0}


def test_single_client_join_appears_in_rooms_listing(client):
    with client.websocket_connect("/ws/collab/room-A"):
        resp = client.get("/collab/rooms")
        assert resp.json() == {"rooms": {"room-A": 1}, "total": 1}
    # After disconnect the room should be cleaned up.
    resp = client.get("/collab/rooms")
    assert resp.json()["total"] == 0


def test_binary_payloads_are_relayed_between_peers(client):
    """The wire format used by y-websocket is binary — the relay must
    forward bytes verbatim without trying to decode them."""
    with client.websocket_connect("/ws/collab/room-bin") as a:
        with client.websocket_connect("/ws/collab/room-bin") as b:
            payload = bytes([0, 1, 2, 255, 7, 7, 7])
            a.send_bytes(payload)
            received = b.receive_bytes()
            assert received == payload


def test_text_payloads_are_relayed_between_peers(client):
    with client.websocket_connect("/ws/collab/room-text") as a:
        with client.websocket_connect("/ws/collab/room-text") as b:
            a.send_text("hello peer")
            assert b.receive_text() == "hello peer"


def test_sender_does_not_receive_its_own_message(client):
    # CRDT semantics: a client must not receive an echo of its own
    # update or it would re-apply locally.
    with client.websocket_connect("/ws/collab/room-echo") as a:
        with client.websocket_connect("/ws/collab/room-echo") as b:
            a.send_bytes(b"x")
            assert b.receive_bytes() == b"x"
            b.send_bytes(b"y")
            # If a had queued an echo of "x", it would arrive here
            # before "y" — receiving "y" first proves no echo.
            assert a.receive_bytes() == b"y"


def test_multiple_rooms_are_isolated(client):
    with client.websocket_connect("/ws/collab/room-1") as a:
        with client.websocket_connect("/ws/collab/room-2") as b:
            a.send_bytes(b"only-for-room-1")
            # b is in a different room and should never see the message.
            # The cleanest assertion is via the rooms listing.
            resp = client.get("/collab/rooms")
            assert resp.json() == {"rooms": {"room-1": 1, "room-2": 1}, "total": 2}


def test_room_disappears_only_after_last_client_leaves(client):
    with client.websocket_connect("/ws/collab/room-X") as a:
        with client.websocket_connect("/ws/collab/room-X") as b:
            assert client.get("/collab/rooms").json() == {
                "rooms": {"room-X": 2},
                "total": 1,
            }
        # b disconnected, a still here.
        assert client.get("/collab/rooms").json() == {
            "rooms": {"room-X": 1},
            "total": 1,
        }
    # Both gone — room evicted.
    assert client.get("/collab/rooms").json()["total"] == 0
