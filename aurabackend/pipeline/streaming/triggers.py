"""
Dataflow Model trigger primitives — Sprint 20a (Pillar 4).

Anchor:
  * Akidau et al. (2015). "The Dataflow Model." PVLDB 8(12).
    Section 4 (Triggers) — separates WHEN a window fires (trigger
    semantics) from WHAT it computes (windowing semantics) from HOW
    refinements relate (accumulation semantics). This module
    implements the WHEN dimension.

What this module ships
----------------------
Pure-function trigger primitives that decide whether a window should
FIRE now. Each trigger is stateless w.r.t. data — they consume only
the trigger context (current watermark, current count of buffered
events, current processing-time clock) and return a boolean.

  * ``WatermarkTrigger``       — fire when watermark passes window-end.
  * ``CountTrigger``           — fire after N events in the window.
  * ``ProcessingTimeTrigger``  — fire after Δ wall-clock since first event.
  * ``CompositeTrigger``       — boolean combinator (ANY-fires / ALL-fire).

Why separate trigger from window?
---------------------------------
Pre-Dataflow streaming systems hard-wired "windows fire at watermark."
That's the right default for analytic correctness but wrong for
latency-sensitive use cases: a 1-hour window can't emit ANY result
for 1 hour. The Dataflow Model decouples them — a count trigger can
emit an early speculative result after 100 events, the watermark
trigger emits the authoritative result an hour later, and an
accumulation mode (DISCARDING / ACCUMULATING / ACCUMULATING_AND_RETRACTING)
decides how the speculative + authoritative results relate.

Sprint 20a ships the trigger primitives; the accumulation modes are
out of scope until S20.1 wires them into a real ``WindowProcessor``.

Determinism
-----------
``WatermarkTrigger`` is fully deterministic (causal: same watermark
sequence → same fire decisions). ``CountTrigger`` is deterministic
in the event-count semantics. ``ProcessingTimeTrigger`` is NOT
deterministic across replays (wall-clock varies) — Sprint 20a flags
this in the trigger's ``is_deterministic`` property so the
audit-engine integration in S20.1 can refuse to use non-deterministic
triggers for hash-stable audit artifacts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Literal


@dataclass(frozen=True)
class TriggerContext:
    """Inputs every trigger evaluates against. Pre-computed by the
    window operator on each pane-evaluation cycle.

    Stateless on the trigger side; the window operator owns the
    state. Trigger ``should_fire`` is a pure function of context."""

    watermark_ts: float
    """Composite watermark from upstream — typically ``WatermarkTracker.composite``."""

    event_count: int
    """Number of events that have arrived in this window so far."""

    processing_ts: float
    """Wall-clock seconds since epoch (``time.time()``)."""

    first_event_processing_ts: float
    """Processing-time when the FIRST event arrived in this window.
    Used by ProcessingTimeTrigger to compute elapsed wall-clock since
    window open. NaN until the first event arrives."""


# ── Trigger base + concrete primitives ────────────────────────────────


class Trigger(ABC):
    """Boolean predicate over TriggerContext."""

    @abstractmethod
    def should_fire(self, ctx: TriggerContext) -> bool:
        """Return True iff the window should fire at this instant."""

    @property
    def is_deterministic(self) -> bool:
        """True iff this trigger fires identically on byte-identical
        event streams across replays. False for any trigger whose
        decision depends on wall-clock."""
        return True


class WatermarkTrigger(Trigger):
    """Fire when the composite watermark crosses ``window_end_ts``.

    This is the Dataflow Model's default trigger for analytic
    correctness: by the time the watermark passes window end, the
    upstream has guaranteed no more in-window events (modulo late
    data, which goes to the late-data policy)."""

    def __init__(self, window_end_ts: float) -> None:
        self._window_end_ts = float(window_end_ts)

    def should_fire(self, ctx: TriggerContext) -> bool:
        return ctx.watermark_ts >= self._window_end_ts

    @property
    def window_end_ts(self) -> float:
        return self._window_end_ts


class CountTrigger(Trigger):
    """Fire as soon as the in-window event count reaches ``threshold``.

    Useful for early speculative results — pair with a WatermarkTrigger
    via ANY-combinator to emit "first 100 events" + "final aggregate
    when watermark closes the window."

    Threshold is the **first-crossing** semantics: fires when count
    transitions from ``< threshold`` to ``>= threshold``. The window
    operator is responsible for resetting trigger state between
    fires (the Dataflow Model trigger ``has_fired`` flag is operator-
    side state, not trigger-side state)."""

    def __init__(self, threshold: int) -> None:
        if threshold < 1:
            raise ValueError(f"CountTrigger threshold must be >= 1, got {threshold}")
        self._threshold = int(threshold)

    def should_fire(self, ctx: TriggerContext) -> bool:
        return ctx.event_count >= self._threshold

    @property
    def threshold(self) -> int:
        return self._threshold


class ProcessingTimeTrigger(Trigger):
    """Fire after ``interval_seconds`` of wall-clock time has elapsed
    since the FIRST event arrived in the window.

    NOT deterministic across replays — flag accordingly. Useful for
    latency-bounded "emit something at least every X seconds even if
    the watermark is still behind" patterns."""

    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                f"ProcessingTimeTrigger interval must be > 0, got {interval_seconds}"
            )
        self._interval = float(interval_seconds)

    def should_fire(self, ctx: TriggerContext) -> bool:
        if ctx.first_event_processing_ts != ctx.first_event_processing_ts:
            # NaN — no events yet
            return False
        return ctx.processing_ts >= ctx.first_event_processing_ts + self._interval

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def is_deterministic(self) -> bool:
        # Processing-time depends on wall-clock; trigger fires at
        # different points across replays even on byte-identical streams.
        return False


class CompositeTrigger(Trigger):
    """Boolean combinator over child triggers.

    Modes:
        * ``"any"`` — fire if ANY child trigger fires (OR-semantics,
                    the Dataflow Model's "trigger any" combinator). Useful
                    for "emit early speculative results OR final on watermark".
        * ``"all"`` — fire only when ALL children fire (AND-semantics,
                    "trigger all" combinator). Useful for "wait for both
                    100 events AND 5 seconds elapsed before firing".

    Determinism: the composite is deterministic iff every child is
    deterministic. A ProcessingTimeTrigger inside an ANY-composite
    makes the whole composite non-deterministic."""

    def __init__(self, children: List[Trigger], mode: Literal["any", "all"]) -> None:
        if not children:
            raise ValueError("CompositeTrigger requires at least one child")
        if mode not in ("any", "all"):
            raise ValueError(f"CompositeTrigger mode must be 'any' or 'all', got {mode!r}")
        self._children = list(children)
        self._mode = mode

    def should_fire(self, ctx: TriggerContext) -> bool:
        if self._mode == "any":
            return any(c.should_fire(ctx) for c in self._children)
        return all(c.should_fire(ctx) for c in self._children)

    @property
    def is_deterministic(self) -> bool:
        return all(c.is_deterministic for c in self._children)

    @property
    def children(self) -> List[Trigger]:
        return list(self._children)

    @property
    def mode(self) -> str:
        return self._mode


__all__ = [
    "TriggerContext",
    "Trigger",
    "WatermarkTrigger",
    "CountTrigger",
    "ProcessingTimeTrigger",
    "CompositeTrigger",
]
