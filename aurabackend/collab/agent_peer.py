"""
Server-side Yjs CRDT peer for AURA agents.

An ``AgentPeer`` participates in the Y protocol exactly like a browser
client does, but runs in the same Python process as the relay. Outbound
local doc updates flow through the relay's broadcast (so humans see
them), inbound updates from humans flow into the agent's local doc (so
the agent's view of the document stays consistent).

Three presence states are exposed via standard y-protocols/awareness:

  - ``idle``      — peer is connected but not currently active
  - ``thinking``  — agent is reasoning (LLM call in flight, no edits yet)
  - ``composing`` — agent is actively typing edits into Y.Text

Humans see these in the same cursor/awareness UI they already use for
each other. No frontend code change required if the existing
``PresenceIndicator`` reads the awareness ``phase`` field.
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from pycrdt import Doc, Text

from .yprotocol import (
    MSG_AWARENESS,
    MSG_SYNC,
    SYNC_UPDATE,
    decode_message,
    encode_awareness_update,
    encode_sync_update,
)

logger = logging.getLogger("aura.collab.agent_peer")


PresenceState = Literal["idle", "thinking", "composing"]

# Async callback the manager hands the peer so outbound updates can be
# fanned out to human websockets in the same room.
SendCallback = Callable[[bytes], Awaitable[None]]


class AgentPeer:
    """One agent in one room, holding one Y.Doc state.

    Lifecycle:
        peer = AgentPeer(room_id, agent_name)
        manager.attach(peer)             # registers + sets _send
        await peer.set_presence("thinking")
        await peer.type_text("sql", "SELECT ...", char_delay_ms=30)
        await peer.set_presence("idle")
        manager.detach(peer)
    """

    def __init__(
        self,
        room_id: str,
        agent_name: str = "AURA Agent",
        color: str = "#7c3aed",
        char_delay_ms: int = 30,
    ) -> None:
        self.room_id = room_id
        self.agent_name = agent_name
        self.color = color
        # y-protocols/awareness clientID is a 32-bit random uint per Yjs convention.
        self.client_id = random.randint(0, 0x7FFFFFFF)
        self.peer_id = uuid.uuid4().hex[:12]
        self.char_delay_ms = char_delay_ms

        self._doc: Doc = Doc()
        self._send: Optional[SendCallback] = None
        self._send_loop: Optional[asyncio.AbstractEventLoop] = None

        self._awareness_clock = 0
        self._presence: PresenceState = "idle"

        # Subscribe to local doc mutations — every text.insert / text.delete
        # triggers this callback synchronously with the encoded update bytes.
        self._observer = self._doc.observe(self._on_local_update)

    # ── Wiring (called by manager.attach / detach) ────────────────────

    def _attach_send(self, send: SendCallback, loop: asyncio.AbstractEventLoop) -> None:
        self._send = send
        self._send_loop = loop

    def _detach_send(self) -> None:
        self._send = None
        self._send_loop = None

    # ── Outbound: local doc → wire ────────────────────────────────────

    def _on_local_update(self, event: Any) -> None:
        """pycrdt fires this synchronously when our doc mutates. We need
        to enqueue the wire bytes onto the event loop so they reach the
        relay's async fan-out."""
        update = getattr(event, "update", None)
        if not update or self._send is None or self._send_loop is None:
            return
        wire = encode_sync_update(update)
        # Schedule the async send from this sync callback.
        try:
            asyncio.run_coroutine_threadsafe(self._send(wire), self._send_loop)
        except RuntimeError:
            # Loop is closing — drop the update silently. The peer is
            # detaching anyway.
            logger.debug("AgentPeer outbound dropped: loop closed")

    # ── Inbound: wire → local doc ─────────────────────────────────────

    def on_inbound(self, payload: bytes) -> None:
        """Called by the manager when a human sends a y-protocol message.
        We apply sync updates to our local Doc; awareness messages are
        not consumed by the agent (the agent only emits its own)."""
        try:
            msg_type, sub_type, body = decode_message(payload)
        except Exception as exc:
            logger.debug("AgentPeer skipped malformed inbound: %s", exc)
            return
        if msg_type != MSG_SYNC:
            return
        if sub_type != SYNC_UPDATE:
            # We don't respond to sync_step1 — the human's first step is
            # to greet us, not the other way around. The relay's pure
            # broadcast already covers human↔human sync; the agent
            # picks up state lazily from the next SYNC_UPDATE.
            return
        try:
            self._doc.apply_update(body)
        except Exception as exc:
            logger.warning("AgentPeer apply_update failed: %s", exc)

    # ── Presence (awareness) ──────────────────────────────────────────

    async def set_presence(self, state: PresenceState) -> None:
        """Broadcast a y-protocols/awareness update with the new state.
        Humans see this in their awareness map under our client_id."""
        if self._send is None:
            return
        self._awareness_clock += 1
        self._presence = state
        payload = encode_awareness_update(
            client_id=self.client_id,
            clock=self._awareness_clock,
            state={
                "name": self.agent_name,
                "color": self.color,
                "phase": state,           # 'idle' | 'thinking' | 'composing'
                "isAgent": True,
                "peerId": self.peer_id,
            },
        )
        await self._send(payload)

    @property
    def presence(self) -> PresenceState:
        return self._presence

    # ── Edits ─────────────────────────────────────────────────────────

    def get_text(self, field: str) -> Text:
        """Get-or-create a Y.Text under the given root field. Same shape
        as the browser-side ``ydoc.getText('sql')`` call."""
        return self._doc.get(field, type=Text)

    async def type_text(
        self,
        field: str,
        text: str,
        *,
        char_delay_ms: Optional[int] = None,
        clear_first: bool = True,
    ) -> None:
        """Type ``text`` into the Y.Text at ``field`` character-by-
        character with realistic delays — humans see each character
        appear via the standard Yjs update stream.

        Awareness is NOT auto-set here so callers can sequence
        ``thinking`` → ``composing`` → ``idle`` around the call.
        """
        delay_s = (char_delay_ms if char_delay_ms is not None else self.char_delay_ms) / 1000.0
        ytext = self.get_text(field)
        if clear_first and len(ytext) > 0:
            # pycrdt Text supports del-by-slice; mutating fires the
            # observer once with a delete update.
            del ytext[: len(ytext)]
            await asyncio.sleep(delay_s)
        for ch in text:
            ytext += ch
            if delay_s > 0:
                await asyncio.sleep(delay_s)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def disconnect(self) -> None:
        """Final awareness broadcast removing this peer from the
        awareness map — humans see the agent leave cleanly instead of
        timing out after the awareness ttl."""
        if self._send is None:
            return
        self._awareness_clock += 1
        # state=None means 'removed' in y-protocols/awareness; an empty
        # state plus a final clock bump is the cleanest portable signal.
        try:
            payload = encode_awareness_update(self.client_id, self._awareness_clock, None)
            await self._send(payload)
        finally:
            self._presence = "idle"
