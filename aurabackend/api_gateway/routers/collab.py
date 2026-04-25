"""Real-time collaboration relay (S6.10 v1).

This router hosts a WebSocket endpoint at ``/ws/collab/{room_id}`` that
acts as a *dumb relay* between connected clients. Messages received from
one client are forwarded verbatim (text or binary) to every other client
in the same room. Most importantly, the server never inspects payloads,
so it is compatible with the `y-websocket
<https://github.com/yjs/y-websocket>`_ wire protocol — clients can drive
a Yjs CRDT document from the browser without the backend needing to know
anything about CRDTs.

What v1 ships:
    * Per-room broadcast (last-writer-wins fan-out, no message ordering).
    * Disconnect cleanup (rooms are removed when empty).
    * Lightweight ``GET /collab/rooms`` introspection endpoint.

What v1 deliberately punts on:
    * Auth — relies on the gateway's existing JWT middleware in front of
      HTTP routes; WS auth (token in query string) is a v2 follow-up
      because the existing JWT middleware short-circuits on the
      ``/ws/`` prefix today.
    * Persistence — Yjs documents live in browser memory only. v2 should
      snapshot to ``metadata_store`` so a refresh re-hydrates state.
    * Awareness presence — y-protocols/awareness rides on the same
      socket and works through this relay unchanged, but no server-side
      presence list is exposed yet.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from itertools import count
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["collab"])

_client_ids = count(1)


class _Room:
    """In-memory record of one collab room.

    Why a class and not a bare dict: we keep an asyncio.Lock per-room so
    fan-out for one room can't be interleaved by a join/leave on the
    same room mid-broadcast. Cross-room operations stay parallel.
    """

    __slots__ = ("clients", "lock")

    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()
        self.lock = asyncio.Lock()


_rooms: Dict[str, _Room] = defaultdict(_Room)
_rooms_lock = asyncio.Lock()


async def _join(room_id: str, ws: WebSocket) -> _Room:
    async with _rooms_lock:
        room = _rooms[room_id]
    async with room.lock:
        room.clients.add(ws)
    return room


async def _leave(room_id: str, ws: WebSocket) -> None:
    async with _rooms_lock:
        room = _rooms.get(room_id)
    if room is None:
        return
    async with room.lock:
        room.clients.discard(ws)
        empty = not room.clients
    if empty:
        async with _rooms_lock:
            # Re-check under the outer lock — a peer may have just joined.
            other = _rooms.get(room_id)
            if other is room and not other.clients:
                _rooms.pop(room_id, None)


async def _broadcast(room: _Room, sender: WebSocket, payload: bytes | str) -> None:
    # Snapshot peers under the room lock so a concurrent leave doesn't
    # mutate the set mid-iteration; release the lock before awaiting
    # sends so a slow client can't stall the room.
    async with room.lock:
        peers = [c for c in room.clients if c is not sender]
    if not peers:
        return
    is_text = isinstance(payload, str)
    results = await asyncio.gather(
        *(c.send_text(payload) if is_text else c.send_bytes(payload) for c in peers),
        return_exceptions=True,
    )
    for peer, result in zip(peers, results):
        if isinstance(result, Exception):
            logger.debug("collab fan-out drop: %s", result)
            try:
                await peer.close()
            except Exception:
                pass


@router.websocket("/ws/collab/{room_id}")
async def collab_socket(websocket: WebSocket, room_id: str) -> None:
    await websocket.accept()
    client_id = next(_client_ids)
    room = await _join(room_id, websocket)
    logger.info("collab join: room=%s client=%s peers=%d", room_id, client_id, len(room.clients))
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            payload: bytes | str | None = msg.get("bytes")
            if payload is None:
                payload = msg.get("text")
            if payload is None:
                continue
            await _broadcast(room, websocket, payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("collab socket error: room=%s client=%s", room_id, client_id)
    finally:
        await _leave(room_id, websocket)
        logger.info("collab leave: room=%s client=%s", room_id, client_id)


@router.get("/collab/rooms")
async def list_rooms() -> dict:
    async with _rooms_lock:
        snapshot = {rid: len(room.clients) for rid, room in _rooms.items()}
    return {"rooms": snapshot, "total": len(snapshot)}
