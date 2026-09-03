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

What v1 deliberately punted on, now closed (BUG-038):
    * Auth — JWTAuthMiddleware (a BaseHTTPMiddleware subclass) never runs
      for WebSocket connections at all (it only intercepts
      ``scope["type"] == "http"``), so the ``/ws/`` prefix was reachable
      with zero authentication regardless of any HTTP allowlist. Verified
      here instead: the token travels as a ``?token=`` query-string param
      (WebSocket clients can't set an Authorization header before the
      handshake) and is decoded with the same ``shared.auth`` JWT the HTTP
      routes use. When ``AURA_JWT_ENABLED`` is armed, a missing/invalid
      token closes the connection before ``room_id`` is ever joined. Rooms
      are also namespaced by the caller's tenant (``org_id`` from the
      verified token, or ``"default"`` in open/dev mode) so two tenants
      using the same room_id string never share a room, and
      ``GET /collab/rooms`` only lists the caller's own tenant's rooms.

What v1 still punts on:
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

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, status

from shared.auth import decode_access_token
from shared.config import settings
from shared.exceptions import AuthenticationError

from .workspaces import _request_tenant

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


def _tenant_from_ws_token(token: str | None) -> str:
    """Derive the caller's tenant from a WebSocket ``?token=`` query param.

    Mirrors ``shared.auth.require_tenant``'s org_id-over-sub precedence, but
    JWTAuthMiddleware never runs for WebSocket connections (it only
    intercepts ``scope["type"] == "http"``), so there is no
    ``request.state.user`` to read here — the token must be decoded
    directly. Raises AuthenticationError on a missing/invalid token; the
    caller closes the socket rather than letting that propagate as a 500.
    """
    if not token:
        raise AuthenticationError("token query parameter required")
    user = decode_access_token(token)
    return str(user.get("org_id") or user.get("sub"))


@router.websocket("/ws/collab/{room_id}")
async def collab_socket(websocket: WebSocket, room_id: str) -> None:
    # BUG-038: JWTAuthMiddleware never reaches this endpoint (see module
    # docstring), so auth is enforced here directly, BEFORE accept() --
    # query_params are available from the initial scope without needing the
    # handshake completed, so an invalid/missing token rejects the
    # connection outright instead of accepting then immediately closing it.
    # Open/dev mode (AURA_JWT_ENABLED=false) keeps v1's behaviour -- every
    # caller shares the "default" tenant namespace, same as the rest of the
    # app's unauthenticated fallback (api_gateway/routers/workspaces.py).
    if settings.jwt_enabled:
        try:
            tenant = _tenant_from_ws_token(websocket.query_params.get("token"))
        except AuthenticationError as exc:
            logger.info("collab join rejected: room=%s reason=%s", room_id, exc)
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    else:
        tenant = "default"

    await websocket.accept()

    # Namespaced so two tenants never share a room merely by picking the
    # same room_id string, and GET /collab/rooms can filter by tenant.
    tenant_room_id = f"{tenant}:{room_id}"

    client_id = next(_client_ids)
    room = await _join(tenant_room_id, websocket)
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
        await _leave(tenant_room_id, websocket)
        logger.info("collab leave: room=%s client=%s", room_id, client_id)


@router.get("/collab/rooms")
async def list_rooms(request: Request) -> dict:
    """List the CALLER's tenant's active rooms.

    BUG-038: previously returned every tenant's room ids and occupancy
    counts unfiltered. Rooms are keyed internally as "<tenant>:<room_id>";
    this strips the prefix back off so the response still shows the
    caller's own room_id strings.
    """
    tenant = _request_tenant(request) or "default"
    prefix = f"{tenant}:"
    async with _rooms_lock:
        snapshot = {
            rid[len(prefix):]: len(room.clients)
            for rid, room in _rooms.items()
            if rid.startswith(prefix)
        }
    return {"rooms": snapshot, "total": len(snapshot)}
