"""
Process-wide registry of active AgentPeers, keyed by room.

The relay router calls into this module on every websocket message so
in-process peers receive the same y-protocol stream that humans do.
Conversely, the manager wires each peer's outbound updates back through
the relay's existing ``_broadcast`` so peer edits reach every human in
the room.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Set

from .agent_peer import AgentPeer

logger = logging.getLogger("aura.collab.manager")


# ── Registry ──────────────────────────────────────────────────────────

_peers_by_room: Dict[str, Set[AgentPeer]] = defaultdict(set)
_lock = asyncio.Lock()


# Type for the relay's broadcast hook — the manager calls this with
# (room_id, payload) when a peer wants to push to humans. Wired via
# ``set_broadcast_hook`` from the relay router during app init.
BroadcastHook = Callable[[str, bytes], Awaitable[None]]
_broadcast_hook: BroadcastHook | None = None


def set_broadcast_hook(hook: BroadcastHook) -> None:
    """Called once by the relay router at module load. The manager
    can't import the relay (would be circular), so the relay registers
    its broadcast function here."""
    global _broadcast_hook
    _broadcast_hook = hook


# ── Attach / detach ───────────────────────────────────────────────────

async def attach(peer: AgentPeer) -> None:
    """Register a peer and wire its outbound send through the relay."""
    if _broadcast_hook is None:
        raise RuntimeError(
            "collab broadcast hook not set — the relay router must "
            "call set_broadcast_hook() before agents attach."
        )
    loop = asyncio.get_running_loop()

    async def _send(payload: bytes) -> None:
        # Outbound from peer → broadcast to humans in the same room.
        # Manager doesn't fan back to other peers in the same room
        # (their local docs already produced this update; replaying
        # would be a no-op CRDT-wise but burns cycles).
        await _broadcast_hook(peer.room_id, payload)  # type: ignore[misc]

    peer._attach_send(_send, loop)
    async with _lock:
        _peers_by_room[peer.room_id].add(peer)
    logger.info("collab agent attached: room=%s peer=%s name=%s",
                peer.room_id, peer.peer_id, peer.agent_name)


async def detach(peer: AgentPeer) -> None:
    """Send the awareness farewell, then unregister."""
    try:
        await peer.disconnect()
    except Exception as exc:
        logger.debug("agent disconnect awareness failed: %s", exc)
    async with _lock:
        bucket = _peers_by_room.get(peer.room_id)
        if bucket is not None:
            bucket.discard(peer)
            if not bucket:
                _peers_by_room.pop(peer.room_id, None)
    peer._detach_send()
    logger.info("collab agent detached: room=%s peer=%s",
                peer.room_id, peer.peer_id)


# ── Inbound feed (called by relay on every client message) ────────────

def relay_to_agents(room_id: str, payload: bytes) -> None:
    """Sync — relay calls this for every byte payload it broadcasts.
    Each agent peer in the room consumes the message into its local
    Doc. Synchronous to keep the relay fast-path simple."""
    bucket = _peers_by_room.get(room_id)
    if not bucket:
        return
    # Snapshot to avoid mutation-during-iteration if a detach races.
    for peer in list(bucket):
        try:
            peer.on_inbound(payload)
        except Exception as exc:
            logger.warning("AgentPeer.on_inbound failed for room=%s: %s", room_id, exc)


def list_peers(room_id: str) -> List[Dict[str, Any]]:
    """Read-only view for /collab/agents endpoint."""
    bucket = _peers_by_room.get(room_id)
    if not bucket:
        return []
    return [
        {
            "peer_id": p.peer_id,
            "agent_name": p.agent_name,
            "client_id": p.client_id,
            "presence": p.presence,
        }
        for p in bucket
    ]


def all_rooms_with_agents() -> Dict[str, int]:
    return {room: len(bucket) for room, bucket in _peers_by_room.items()}
