"""
Asynchronous Barrier Snapshotting (ABS) primitives for AURA — Sprint 20a
(Pillar 4: Distributed Streaming Fabric).

Anchors:
  * Carbone, Ewen, Fora, Haridi, Richter, Tzoumas (2015). "Lightweight
    Asynchronous Snapshots for Distributed Dataflows." arXiv:1506.08603 /
    Apache Flink's checkpoint mechanism. https://arxiv.org/abs/1506.08603
  * Chandy & Lamport (1985). "Distributed Snapshots: Determining Global
    States of Distributed Systems." ACM TOCS 3(1):63-75 — the underlying
    distributed-snapshot theorem ABS optimises.

What this module ships
----------------------
Standalone primitives — NOT YET wired into the live streaming_engine.
Sprint 20a ships algorithmic correctness; integration is the S20.1
follow-up (per the lesson from Sprint 18: don't bundle primitive
correctness with worker-integration risk).

  * ``BarrierMarker``  — Pydantic model that travels through the stream
    alongside data, identifying snapshot boundaries by ``barrier_id``.
  * ``BarrierAligner`` — per-operator state machine implementing
    Chandy-Lamport alignment over multiple input channels: blocks each
    channel after delivering a barrier_id, fires ``ALIGNED`` once all
    channels have delivered the same barrier_id, then releases buffered
    messages back to the operator.

Why barrier alignment matters (the ABS theorem)
-----------------------------------------------
The wall-clock checkpoint that AURA's ``StateManager.should_checkpoint``
currently uses captures snapshots at arbitrary moments — there is NO
guarantee that the snapshots taken across operators correspond to the
SAME global state. Two consecutive snapshots taken 30s apart on
operators A and B may have processed different prefixes of the input
stream; on failure recovery, A and B replay different starting points
and downstream output is non-deterministic.

Carbone et al. solve this with **alignment**: a barrier marker is
injected into every input source on a configurable cadence (the source
emits, e.g., ``BarrierMarker(barrier_id=42)`` every 30s). Operators
that have multiple input channels (joins, unions) BLOCK each channel
after it delivers barrier_42; the messages after barrier_42 on that
channel are BUFFERED but not processed. Once all input channels have
delivered barrier_42, the operator is "aligned" — it snapshots its
state, forwards barrier_42 to all downstream channels, then releases
the buffered messages.

The Chandy-Lamport invariant guarantees the snapshot is consistent:
every message ``m`` is in EXACTLY ONE snapshot's "in-flight" set across
the entire DAG. On recovery, replaying from the latest aligned snapshot
produces byte-identical downstream output — the **exactly-once
processing** guarantee Apache Flink ships against ABS.

Hot-path design
---------------
``BarrierAligner.receive_message`` is the per-message inner loop; it
returns a ``RouteAction`` (PROCESS or BUFFER) in O(1) time so the
operator's outer loop doesn't slow down. ``receive_barrier`` is called
once per input channel per barrier_id — typically every 30s — so its
linear-scan checks over the input-channel set are cheap.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

# ── Pydantic-ish model for on-the-wire barrier markers ────────────────
# Plain dataclass; the streaming engine serialises to JSON for the
# pub-sub bus. Keep it lightweight so it's cheap to inject every 30s
# into each source.


@dataclass(frozen=True)
class BarrierMarker:
    """A barrier that travels through the stream alongside data.

    Sources inject one of these every ``barrier_interval_seconds`` (the
    operator declares the cadence). Operators recognise these as
    NON-DATA control elements — they do NOT carry payload, they only
    carry the ``barrier_id`` that the alignment state machine watches.
    """

    barrier_id: int
    """Monotonically increasing identifier — barrier 42 strictly precedes
    barrier 43 in the same stream. Operators use this to detect
    multiple in-flight barriers (when the source injects faster than
    the slowest operator can align)."""

    timestamp_iso: str
    """ISO-8601 wall-clock when the source injected this barrier.
    Used only for telemetry — the alignment algorithm is purely
    causal (based on channel arrival order), not wall-clock."""

    source_id: str = ""
    """Optional source identifier. When operators have multiple sources
    feeding the same input channel (rare but valid), this disambiguates
    'barrier 42 from source A' vs 'barrier 42 from source B'."""


# ── Alignment state machine ───────────────────────────────────────────


class AlignmentEvent(Enum):
    """What ``receive_barrier`` tells the caller to do next."""

    BUFFERED = "buffered"
    """The barrier was recorded for this channel, but not all channels
    have delivered it yet. The operator should continue accepting data
    from channels that haven't yet delivered this barrier_id. Channels
    that have delivered are now blocked (subsequent messages from them
    return BUFFER from receive_message)."""

    ALIGNED = "aligned"
    """All input channels have now delivered this barrier_id. The
    operator MUST: (1) take a snapshot of its state, (2) forward the
    barrier to all downstream channels, (3) call ``emit_buffered``
    to drain the buffered post-barrier messages back into the operator
    loop, (4) resume normal processing."""

    DUPLICATE = "duplicate"
    """This channel already delivered this barrier_id. Indicates a
    misconfigured source (duplicate injection) — caller should log
    and ignore. Idempotent so a network retry doesn't corrupt the
    alignment state."""


class RouteAction(Enum):
    """What ``receive_message`` tells the caller to do with a message."""

    PROCESS = "process"
    """No pending barriers on this channel — operator processes the
    message immediately."""

    BUFFER = "buffer"
    """A barrier has been delivered on this channel but alignment
    hasn't fired yet. The operator must hold this message until
    ``emit_buffered`` releases it (post-snapshot)."""


@dataclass
class _ChannelState:
    """Per-channel alignment state. One instance per input channel."""

    delivered_barriers: Set[int] = field(default_factory=set)
    """Set of barrier_ids this channel has delivered. A channel is
    "blocked" with respect to barrier_id N iff N is in this set AND
    not all OTHER channels have N in their set."""


class BarrierAligner:
    """Per-operator Chandy-Lamport alignment over N input channels.

    Thread-safe: a single lock protects the state machine because most
    deployments will call ``receive_message`` from a single coroutine
    per channel; contention is on the cross-channel ``receive_barrier``
    transitions which are rare (one per channel per barrier interval).

    Usage::

        aligner = BarrierAligner(input_channels=["upstream_A", "upstream_B"])

        # On each incoming message from channel `c`:
        action = aligner.receive_message(channel=c)
        if action is RouteAction.PROCESS:
            operator.process(msg)
        else:
            local_buffer.append((c, msg))

        # On each incoming barrier marker from channel `c`:
        event = aligner.receive_barrier(channel=c, barrier_id=marker.barrier_id)
        if event is AlignmentEvent.ALIGNED:
            snapshot = operator.snapshot_state()
            checkpoint_store.persist(snapshot, marker.barrier_id)
            for downstream_ch in downstream_channels:
                downstream_ch.send(marker)
            for c2, msg in aligner.emit_buffered(marker.barrier_id):
                operator.process(msg)

    The caller does the actual snapshot + forward + buffer-drain; this
    class is purely a state machine, NOT an I/O actor — keeps it
    composable with whichever transport the engine ends up using.
    """

    def __init__(self, input_channels: List[str]) -> None:
        if not input_channels:
            raise ValueError("BarrierAligner requires at least one input channel")
        if len(set(input_channels)) != len(input_channels):
            raise ValueError(f"input_channels must be unique: {input_channels}")
        self._channels: Dict[str, _ChannelState] = {
            c: _ChannelState() for c in input_channels
        }
        # Buffered messages per (channel, pending_barrier_id) — released
        # by emit_buffered once that barrier_id aligns.
        self._buffers: Dict[int, Dict[str, list]] = {}
        # barrier_ids that have completed alignment — short retention
        # so a late duplicate barrier is correctly flagged as DUPLICATE
        # rather than restarting alignment.
        self._completed: Set[int] = set()
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────

    def receive_message(self, channel: str) -> RouteAction:
        """Decide whether the operator should PROCESS or BUFFER the
        message arriving on ``channel`` right now.

        A channel is BLOCKED if it has delivered any barrier_id that
        hasn't yet aligned across all channels. Per Carbone et al.
        § 3.1, this preserves the Chandy-Lamport invariant that no
        post-barrier message contributes to the pre-barrier snapshot.
        """
        with self._lock:
            self._check_known_channel(channel)
            pending = self._pending_barriers_for(channel)
            if not pending:
                return RouteAction.PROCESS
            # Buffer under the LOWEST pending barrier_id — releases when
            # that barrier aligns. Subsequent barriers stack naturally:
            # buffer under barrier N first; when N aligns the operator
            # processes those, then buffer under N+1, etc.
            self._buffers.setdefault(min(pending), {}).setdefault(channel, [])
            return RouteAction.BUFFER

    def buffer(self, channel: str, message: object) -> None:
        """Persist a message that ``receive_message`` said to BUFFER.

        Convenience helper — the caller could maintain its own buffer
        but the natural keying is by (pending_barrier_id, channel),
        which this aligner already knows. ``emit_buffered`` will
        return these in original arrival order per channel.
        """
        with self._lock:
            self._check_known_channel(channel)
            pending = self._pending_barriers_for(channel)
            if not pending:
                raise RuntimeError(
                    f"buffer() called on channel {channel!r} with no pending barrier — "
                    f"receive_message returned PROCESS for this channel, callers must "
                    f"only buffer when receive_message returned BUFFER."
                )
            self._buffers.setdefault(min(pending), {}).setdefault(channel, []).append(message)

    def receive_barrier(self, channel: str, barrier_id: int) -> AlignmentEvent:
        """Mark that ``channel`` has now delivered ``barrier_id``.

        Returns:
            * BUFFERED — barrier recorded; not all channels have aligned yet.
              Channel ``channel`` is now BLOCKED w.r.t. ``barrier_id``.
            * ALIGNED  — all channels have delivered ``barrier_id``;
              caller must snapshot, forward, and call ``emit_buffered``.
            * DUPLICATE — ``channel`` already delivered ``barrier_id``;
              caller should ignore. Idempotent under retransmission.
        """
        with self._lock:
            self._check_known_channel(channel)
            if barrier_id in self._completed:
                # Already aligned; this is a late retransmission. Idempotent.
                return AlignmentEvent.DUPLICATE
            state = self._channels[channel]
            if barrier_id in state.delivered_barriers:
                return AlignmentEvent.DUPLICATE
            state.delivered_barriers.add(barrier_id)
            # Check alignment: all channels must have delivered.
            if all(barrier_id in s.delivered_barriers for s in self._channels.values()):
                # Move barrier from per-channel pending → completed; the
                # operator will call emit_buffered to drain that barrier's
                # buffer. Caller owns the snapshot + forward duties.
                for s in self._channels.values():
                    s.delivered_barriers.discard(barrier_id)
                self._completed.add(barrier_id)
                self._prune_completed()
                return AlignmentEvent.ALIGNED
            return AlignmentEvent.BUFFERED

    def emit_buffered(self, barrier_id: int) -> List[tuple]:
        """Drain the buffered messages associated with the just-aligned
        ``barrier_id`` in (channel, message) order.

        Returned in deterministic order:
            * channels are emitted in the order ``input_channels`` was
              passed to ``__init__`` (preserves operator-author intent),
            * within a channel, messages preserve arrival order.

        Caller invokes this AFTER ``receive_barrier`` returned ALIGNED
        and AFTER the snapshot has been persisted; calling out-of-order
        breaks the ABS exactly-once guarantee.
        """
        with self._lock:
            buf = self._buffers.pop(barrier_id, {})
            ordered: List[tuple] = []
            for channel_name in self._channels:
                for msg in buf.get(channel_name, ()):
                    ordered.append((channel_name, msg))
            return ordered

    # ── Introspection (for tests + Prometheus gauges) ────────────────

    @property
    def channels(self) -> List[str]:
        """Read-only list of registered input channel names."""
        return list(self._channels.keys())

    def pending_alignment(self) -> Dict[int, List[str]]:
        """For each in-flight barrier_id, which channels have NOT yet
        delivered it. Empty list means alignment is one channel away."""
        with self._lock:
            all_ids: Set[int] = set()
            for s in self._channels.values():
                all_ids.update(s.delivered_barriers)
            return {
                bid: [
                    c for c, s in self._channels.items()
                    if bid not in s.delivered_barriers
                ]
                for bid in sorted(all_ids)
            }

    def buffered_count(self) -> int:
        """Total messages currently buffered across all pending barriers.
        Used by Prometheus to detect a slow-aligning channel before it
        blows out memory."""
        with self._lock:
            return sum(
                sum(len(msgs) for msgs in per_channel.values())
                for per_channel in self._buffers.values()
            )

    # ── Internals ────────────────────────────────────────────────────

    def _check_known_channel(self, channel: str) -> None:
        if channel not in self._channels:
            raise KeyError(
                f"unknown input channel {channel!r}; expected one of {list(self._channels)}"
            )

    def _pending_barriers_for(self, channel: str) -> List[int]:
        """barrier_ids that ``channel`` has delivered but that haven't
        aligned across all channels yet — i.e., this channel is
        BLOCKED w.r.t. these barriers."""
        return sorted(self._channels[channel].delivered_barriers)

    def _prune_completed(self) -> None:
        """Keep the last 32 completed barrier_ids for DUPLICATE detection;
        beyond that, retransmissions are exceedingly unlikely and the
        memory cost of an unbounded set adds up."""
        if len(self._completed) > 32:
            to_keep = sorted(self._completed)[-32:]
            self._completed = set(to_keep)


__all__ = [
    "BarrierMarker",
    "BarrierAligner",
    "AlignmentEvent",
    "RouteAction",
]
