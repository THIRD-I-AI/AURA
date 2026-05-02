"""
y-websocket wire protocol helpers (subset).

Implements just enough of the y-protocols/sync and y-protocols/awareness
encoding for an in-process agent peer to emit valid messages that any
y-websocket browser client will accept.

Reference: https://github.com/yjs/y-protocols
"""
from __future__ import annotations

import json
from typing import Tuple

# Top-level message types
MSG_SYNC = 0
MSG_AWARENESS = 1

# y-protocols/sync sub-types
SYNC_STEP_1 = 0   # client → server: send my state vector
SYNC_STEP_2 = 1   # server → client: here's the diff you need
SYNC_UPDATE = 2   # bidirectional: a regular update


# ── varuint codec ────────────────────────────────────────────────────
# Yjs uses unsigned LEB128. 7 data bits per byte, MSB=1 means continue.

def write_varuint(n: int) -> bytes:
    if n < 0:
        raise ValueError("varuint cannot be negative")
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)
    return bytes(out)


def read_varuint(buf: bytes, offset: int = 0) -> Tuple[int, int]:
    """Decode one varuint, return (value, bytes_consumed_from_offset)."""
    n = 0
    shift = 0
    consumed = 0
    while True:
        if offset + consumed >= len(buf):
            raise ValueError("truncated varuint")
        b = buf[offset + consumed]
        consumed += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, consumed
        shift += 7
        if shift > 63:
            raise ValueError("varuint too large")


def write_varbytes(b: bytes) -> bytes:
    return write_varuint(len(b)) + b


def read_varbytes(buf: bytes, offset: int = 0) -> Tuple[bytes, int]:
    length, lenbytes = read_varuint(buf, offset)
    start = offset + lenbytes
    end = start + length
    if end > len(buf):
        raise ValueError("truncated varbytes")
    return buf[start:end], end - offset


# ── sync messages ────────────────────────────────────────────────────

def encode_sync_update(update: bytes) -> bytes:
    """Wrap a raw Y document update in a sync-protocol message ready to
    broadcast through the relay. This is what AgentPeer emits whenever
    its local doc mutates."""
    return bytes([MSG_SYNC, SYNC_UPDATE]) + write_varbytes(update)


def decode_message(payload: bytes) -> Tuple[int, int, bytes]:
    """Decode a top-level message into (msg_type, sub_type, body).

    For MSG_AWARENESS there's no sub-type — sub_type is returned as -1
    and ``body`` is the full awareness payload (varbytes-prefixed).
    """
    if not payload:
        raise ValueError("empty payload")
    msg_type = payload[0]
    if msg_type == MSG_SYNC:
        if len(payload) < 2:
            raise ValueError("truncated sync message")
        sub_type = payload[1]
        body, _ = read_varbytes(payload, 2)
        return msg_type, sub_type, body
    if msg_type == MSG_AWARENESS:
        body, _ = read_varbytes(payload, 1)
        return msg_type, -1, body
    return msg_type, -1, payload[1:]


# ── awareness messages ───────────────────────────────────────────────
# y-protocols/awareness encoding for ONE client emitting its own state:
#
#   [count=1]
#   [clientID]
#   [clock]
#   [len][stateJSON]

def encode_awareness_update(client_id: int, clock: int, state: dict | None) -> bytes:
    """Encode an awareness update for a single client. ``state=None``
    signals the client has gone away (treated as 'remove' by peers)."""
    state_bytes = json.dumps(state if state is not None else {}).encode("utf-8")
    body = (
        write_varuint(1)                     # count of clients
        + write_varuint(client_id)
        + write_varuint(clock)
        + write_varbytes(state_bytes)
    )
    return bytes([MSG_AWARENESS]) + write_varbytes(body)
