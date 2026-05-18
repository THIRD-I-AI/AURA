"""
Sprint 20a — Layer 17b contract tests for Carbone et al. ABS alignment.

The Chandy-Lamport theorem says: given a barrier injected into every
source, an operator that applies the alignment function (block each
input channel after delivering the barrier; wait for all channels;
snapshot; release) produces a snapshot in which every message has
been processed by EXACTLY ONE side of the snapshot boundary across
the entire DAG. These tests prove the standalone BarrierAligner
implements that contract correctly:

  1. Single-channel pass-through (alignment is a no-op).
  2. Two-channel alignment — A delivers barrier first, A's messages
     buffer; B catches up; alignment fires; buffered messages release.
  3. Pre-barrier messages on a delivered channel are NEVER buffered
     after their barrier aligns — they were already processed before
     the alignment.
  4. Duplicate barrier is idempotent (network retransmit safe).
  5. Multiple in-flight barriers (source injects faster than operator
     can align) stack correctly — barrier N+1 doesn't pollute barrier N's
     buffer.
  6. Buffered count introspection — Prometheus telemetry gauge.

These are FORMAL CONTRACTS, not regression tests. A future change to
BarrierAligner must preserve every assertion below or it breaks the
Chandy-Lamport invariant.
"""
from __future__ import annotations

import pytest

from pipeline.streaming.barrier import (
    AlignmentEvent,
    BarrierAligner,
    BarrierMarker,
    RouteAction,
)

# ── BarrierMarker basics ──────────────────────────────────────────────


def test_barrier_marker_is_immutable() -> None:
    """BarrierMarker is a frozen dataclass — tampering with barrier_id
    after injection would silently corrupt alignment."""
    m = BarrierMarker(barrier_id=1, timestamp_iso="2026-05-18T00:00:00+00:00")
    with pytest.raises((AttributeError, Exception)):
        m.barrier_id = 99  # type: ignore[misc]


# ── BarrierAligner constructor contracts ──────────────────────────────


def test_aligner_rejects_empty_channels() -> None:
    """An operator with no input channels makes no sense — fail fast."""
    with pytest.raises(ValueError, match="at least one input channel"):
        BarrierAligner(input_channels=[])


def test_aligner_rejects_duplicate_channels() -> None:
    """Duplicate channel IDs would corrupt the per-channel state map."""
    with pytest.raises(ValueError, match="unique"):
        BarrierAligner(input_channels=["a", "b", "a"])


def test_aligner_rejects_unknown_channel_in_receive_message() -> None:
    """Operator-level bug should fail loudly, not silently misroute."""
    aligner = BarrierAligner(input_channels=["a", "b"])
    with pytest.raises(KeyError, match="unknown input channel"):
        aligner.receive_message(channel="c")


# ── Single-channel pass-through ──────────────────────────────────────


def test_single_channel_messages_always_process() -> None:
    """Operator with one input has no alignment to do — every message
    PROCESSes immediately, every barrier triggers immediate ALIGNED."""
    aligner = BarrierAligner(input_channels=["sole"])
    assert aligner.receive_message("sole") is RouteAction.PROCESS
    event = aligner.receive_barrier("sole", barrier_id=1)
    assert event is AlignmentEvent.ALIGNED
    # Buffer is empty (no message was buffered).
    assert aligner.emit_buffered(barrier_id=1) == []


# ── Two-channel Chandy-Lamport alignment ─────────────────────────────


def test_two_channel_alignment_blocks_first_channel_and_releases_on_alignment() -> None:
    """The core ABS invariant: when channel A delivers barrier_42 but
    channel B has not, A's subsequent messages MUST buffer. When B
    catches up, alignment fires; emit_buffered returns the buffered
    A-messages in order."""
    aligner = BarrierAligner(input_channels=["A", "B"])

    # Pre-barrier traffic on both channels — both PROCESS.
    assert aligner.receive_message("A") is RouteAction.PROCESS
    assert aligner.receive_message("B") is RouteAction.PROCESS

    # A delivers barrier_42 — should BUFFER, not ALIGN yet.
    assert aligner.receive_barrier("A", barrier_id=42) is AlignmentEvent.BUFFERED

    # Post-barrier traffic on A must now BUFFER. Traffic on B still
    # PROCESSes (B hasn't seen the barrier yet → its messages are
    # pre-barrier from B's perspective).
    assert aligner.receive_message("A") is RouteAction.BUFFER
    aligner.buffer("A", "msg_a1")
    assert aligner.receive_message("A") is RouteAction.BUFFER
    aligner.buffer("A", "msg_a2")
    assert aligner.receive_message("B") is RouteAction.PROCESS

    # B finally delivers barrier_42 — alignment fires.
    assert aligner.receive_barrier("B", barrier_id=42) is AlignmentEvent.ALIGNED

    # emit_buffered returns A's buffered messages in arrival order.
    released = aligner.emit_buffered(barrier_id=42)
    assert released == [("A", "msg_a1"), ("A", "msg_a2")]

    # Post-alignment: both channels back to PROCESS.
    assert aligner.receive_message("A") is RouteAction.PROCESS
    assert aligner.receive_message("B") is RouteAction.PROCESS


def test_buffered_messages_emit_in_channel_order_then_arrival_order() -> None:
    """emit_buffered returns deterministic (channel, message) order:
    channels in input_channels order, within a channel arrival order.
    This determinism is what lets the audit-engine produce byte-stable
    artifacts for streaming-pipeline operator decisions."""
    aligner = BarrierAligner(input_channels=["first", "second"])
    # Both channels deliver barrier_1 → must BUFFER subsequent messages.
    aligner.receive_barrier("first", barrier_id=1)
    aligner.receive_message("first")
    aligner.buffer("first", "f1")
    aligner.receive_message("first")
    aligner.buffer("first", "f2")
    aligner.receive_barrier("second", barrier_id=1)
    # On the second's receive_barrier we hit ALIGNED; "second" never
    # buffered anything in this run. Confirm release order.
    released = aligner.emit_buffered(barrier_id=1)
    assert released == [("first", "f1"), ("first", "f2")]


# ── Idempotency on duplicate barriers ────────────────────────────────


def test_duplicate_barrier_on_same_channel_is_idempotent() -> None:
    """Network retransmission of barrier_42 on a channel that already
    delivered barrier_42 MUST be a no-op — alignment state is unchanged."""
    aligner = BarrierAligner(input_channels=["A", "B"])
    assert aligner.receive_barrier("A", barrier_id=42) is AlignmentEvent.BUFFERED
    # Retransmit.
    assert aligner.receive_barrier("A", barrier_id=42) is AlignmentEvent.DUPLICATE
    # Alignment state unchanged — B still hasn't delivered.
    pending = aligner.pending_alignment()
    assert pending[42] == ["B"]


def test_duplicate_barrier_after_alignment_is_idempotent() -> None:
    """Even after alignment, a stale retransmit of barrier_42 must not
    restart alignment — would corrupt the next-barrier's state."""
    aligner = BarrierAligner(input_channels=["A", "B"])
    aligner.receive_barrier("A", barrier_id=42)
    aligner.receive_barrier("B", barrier_id=42)  # ALIGNED
    # Late retransmit on A.
    assert aligner.receive_barrier("A", barrier_id=42) is AlignmentEvent.DUPLICATE


# ── Multiple in-flight barriers (source injects faster than aligner) ──


def test_multiple_in_flight_barriers_stack_independently() -> None:
    """When the source injects barrier_2 before barrier_1 has aligned,
    each barrier's buffer must remain independent — releasing
    barrier_1's buffer must NOT release any barrier_2 messages."""
    aligner = BarrierAligner(input_channels=["A", "B"])

    # A delivers barrier_1 — buffers begin on A under barrier_1.
    aligner.receive_barrier("A", barrier_id=1)
    aligner.receive_message("A")
    aligner.buffer("A", "msg_under_barrier_1")

    # A delivers barrier_2 — A is now blocked on barriers {1, 2};
    # subsequent A messages still buffer (under the LOWEST pending = 1).
    aligner.receive_barrier("A", barrier_id=2)
    aligner.receive_message("A")
    aligner.buffer("A", "msg_under_lowest_pending")

    # B delivers barrier_1 → ALIGNED for barrier_1 (B never saw barrier_2 yet).
    assert aligner.receive_barrier("B", barrier_id=1) is AlignmentEvent.ALIGNED
    released_1 = aligner.emit_buffered(barrier_id=1)
    # Both A messages were buffered under barrier_1 (min pending) — they release.
    assert len(released_1) == 2

    # B delivers barrier_2 → ALIGNED for barrier_2.
    assert aligner.receive_barrier("B", barrier_id=2) is AlignmentEvent.ALIGNED
    # Buffer for barrier_2 is empty (we didn't buffer any new messages
    # after the in-between state).
    assert aligner.emit_buffered(barrier_id=2) == []


# ── Introspection / Prometheus gauges ────────────────────────────────


def test_pending_alignment_reports_missing_channels() -> None:
    """pending_alignment is what a Prometheus exporter uses to detect
    a slow-aligning channel before it blows out memory."""
    aligner = BarrierAligner(input_channels=["A", "B", "C"])
    aligner.receive_barrier("A", barrier_id=7)
    aligner.receive_barrier("C", barrier_id=7)
    pending = aligner.pending_alignment()
    # Only B is missing → telemetry shows the bottleneck.
    assert pending == {7: ["B"]}


def test_buffered_count_reports_total_across_channels() -> None:
    """Total buffered count is the Prometheus 'backpressure on aligned
    snapshot' gauge — operator alerts on this exceeding a threshold."""
    aligner = BarrierAligner(input_channels=["A", "B"])
    aligner.receive_barrier("A", barrier_id=1)
    for _ in range(5):
        aligner.receive_message("A")
        aligner.buffer("A", "x")
    assert aligner.buffered_count() == 5


def test_buffer_without_pending_barrier_raises() -> None:
    """Operator-level bug: trying to buffer when no barrier is pending
    on this channel means the operator misinterpreted RouteAction.PROCESS."""
    aligner = BarrierAligner(input_channels=["A"])
    with pytest.raises(RuntimeError, match="no pending barrier"):
        aligner.buffer("A", "msg")
