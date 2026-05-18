"""
Dataflow Model late-data policies — Sprint 20a (Pillar 4).

Anchor:
  * Akidau et al. (2015). "The Dataflow Model." PVLDB 8(12),
    Section 2.3 + Section 5 — what to do with events that arrive
    AFTER the watermark has closed their event-time window.

What this module ships
----------------------
Pure-function policies that take an event + window + watermark and
return whether the event is ACCEPTED into the window or DROPPED, plus
an optional "side output" for downstream observability.

  * ``drop_policy``                                 — silent drop.
  * ``side_output_policy``                          — drop from main, emit to side channel.
  * ``remerge_within_allowed_lateness_policy(L)``   — accept if  watermark - event_ts <= L,
                                                       else drop OR side-output.

The window operator calls one of these on every event whose
``event_ts < window.start_ts`` OR (more usefully) when the watermark
has already passed ``window.end_ts`` when the event arrives.

Why three policies, not one?
----------------------------
Akidau et al. § 5 catalogues three industry-observed behaviours:

1. **Drop** — what a "no late data" pipeline does. Maximises
   throughput, sacrifices completeness. Fine for streaming dashboards
   where the last 100ms of data don't matter.

2. **Side output** — what a "late-data sink" pipeline does (the
   classic Flink ``OutputTag`` pattern). Keeps the main path's
   determinism intact while preserving late records for off-line
   reconciliation / audit.

3. **Remerge within allowed lateness** — what a "speculative + late
   refinement" pipeline does. The window initially fires on
   watermark, then any late event arriving within ``allowed_lateness``
   seconds is re-merged into the window's state and a refinement is
   emitted downstream. After ``allowed_lateness`` expires, the
   policy degrades to drop OR side-output (caller chooses).

Return contract
---------------
Every policy is a callable returning a ``LateDataDecision`` dataclass:

    LateDataDecision(
        accept_to_window: bool,   # True = merge into window state; False = no-op for main path
        side_output: Optional[Event],  # None = no side output; Event = emit this to side channel
        is_within_lateness: bool, # diagnostic — whether the watermark - event_ts gap was inside L
    )

The window operator interprets:
    * (True, None, *)   → re-fire the window with the late event included
    * (False, evt, *)   → emit ``evt`` to the side-output channel
    * (False, None, *)  → silently drop the event

Side-channel events are tagged with diagnostic fields so audit-side
tooling can reconstruct WHY they were diverted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class LateDataDecision:
    """What the window operator should do with a late event."""

    accept_to_window: bool
    """If True, the operator merges the event into the window's state
    and may refire (Dataflow accumulation modes decide whether the
    refire emits a refinement or replaces the prior result). If
    False, the event does NOT contribute to the window's output on
    the main path."""

    side_output: Optional[Any] = None
    """Optional event to emit on the side-output channel. Typically a
    dict ``{"event": original_event, "reason": str, ...}`` so audit
    tooling can correlate against the main path."""

    is_within_lateness: bool = False
    """Diagnostic: whether the event arrived within an allowed-lateness
    window (if the policy enforces one). Not used by the operator's
    decision logic — purely for telemetry / Prometheus gauges."""


# ── Concrete policy factories ─────────────────────────────────────────


LatePolicy = Callable[[Any, float, float], LateDataDecision]
"""Type alias: a policy maps (event, event_ts, watermark_ts) → decision.

``event_ts`` is the event's event-time (extracted by the operator from
the event's ``timestamp`` field). ``watermark_ts`` is the current
composite watermark."""


def drop_policy(event: Any, event_ts: float, watermark_ts: float) -> LateDataDecision:
    """Silently drop every late event.

    Right choice when the pipeline cares more about latency than
    completeness — dashboards, alerting on monotone metrics, ML
    feature stores with a strict cut-off.
    """
    return LateDataDecision(
        accept_to_window=False,
        side_output=None,
        is_within_lateness=False,
    )


def side_output_policy(
    event: Any, event_ts: float, watermark_ts: float,
) -> LateDataDecision:
    """Divert late events to a side channel for offline reconciliation.

    The Flink ``OutputTag`` / Apache Beam ``with_outputs`` pattern.
    Keeps the main path deterministic (no late merges, no re-fires)
    while preserving every late record for audit / replay / training
    data correction.
    """
    return LateDataDecision(
        accept_to_window=False,
        side_output={
            "event": event,
            "event_ts": event_ts,
            "watermark_ts": watermark_ts,
            "lateness_seconds": watermark_ts - event_ts,
            "reason": "late_event_side_output",
        },
        is_within_lateness=False,
    )


def remerge_within_allowed_lateness_policy(
    allowed_lateness_seconds: float,
    *,
    on_expiry: str = "drop",
) -> LatePolicy:
    """Build a policy that re-merges late events into the window if
    they arrive within ``allowed_lateness_seconds`` of the watermark,
    and falls back to ``on_expiry`` policy (``"drop"`` or
    ``"side_output"``) once that lateness window has closed.

    Args:
        allowed_lateness_seconds: How long after the watermark passes
            the event-time the operator will still accept late events.
            Must be > 0; allowed_lateness == 0 is equivalent to
            ``drop_policy``.
        on_expiry: Behaviour for events that arrive AFTER the
            allowed-lateness window has closed. ``"drop"`` silently
            discards; ``"side_output"`` emits to the side channel
            with reason ``late_beyond_allowed_lateness``.

    Returns a closure suitable for the operator's late-data hook.

    Refer Akidau et al. § 5 Figure 7 for the "Allowed Lateness +
    Side Output" pattern this implements.
    """
    if allowed_lateness_seconds < 0:
        raise ValueError(
            f"allowed_lateness_seconds must be >= 0, got {allowed_lateness_seconds}"
        )
    if on_expiry not in ("drop", "side_output"):
        raise ValueError(
            f"on_expiry must be 'drop' or 'side_output', got {on_expiry!r}"
        )

    def _policy(event: Any, event_ts: float, watermark_ts: float) -> LateDataDecision:
        lateness = watermark_ts - event_ts
        within = lateness <= allowed_lateness_seconds
        if within:
            return LateDataDecision(
                accept_to_window=True,
                side_output=None,
                is_within_lateness=True,
            )
        # Beyond allowed lateness — degrade to the chosen on_expiry policy.
        if on_expiry == "drop":
            return LateDataDecision(
                accept_to_window=False,
                side_output=None,
                is_within_lateness=False,
            )
        # side_output
        return LateDataDecision(
            accept_to_window=False,
            side_output={
                "event": event,
                "event_ts": event_ts,
                "watermark_ts": watermark_ts,
                "lateness_seconds": lateness,
                "allowed_lateness_seconds": allowed_lateness_seconds,
                "reason": "late_beyond_allowed_lateness",
            },
            is_within_lateness=False,
        )

    return _policy


__all__ = [
    "LateDataDecision",
    "LatePolicy",
    "drop_policy",
    "side_output_policy",
    "remerge_within_allowed_lateness_policy",
]
