"""
Dataflow Model composite watermark tracker — Sprint 20a (Pillar 4).

Anchor:
  * Akidau, Bradshaw, Chambers, Chernyak, Fernandez-Moctezuma, Lax,
    McVeety, Mills, Perry, Schmidt, Whittle (2015). "The Dataflow
    Model: A Practical Approach to Balancing Correctness, Latency,
    and Cost in Massive-Scale, Unbounded, Out-of-Order Data
    Processing." PVLDB 8(12):1792-1803. https://dl.acm.org/doi/10.14778/2824032.2824076

What this module ships
----------------------
``WatermarkTracker`` — a per-operator primitive that aggregates
watermarks from N upstream operators into a single composite watermark
following the Dataflow Model invariant:

    W_composite = min(W_upstream_1, W_upstream_2, ..., W_upstream_N)

The composite watermark is what downstream windows compare against to
decide whether their event-time end has been "fully observed." A
window fires when ``W_composite >= window_end_ts`` — at that moment
the Dataflow Model guarantees that no upstream operator will emit any
NEW event with ``timestamp < W_composite`` (modulo late-data policies,
handled by ``late_data.py``).

Why ``min``, not ``max``?
-------------------------
A downstream operator can only safely close a window when EVERY
upstream operator has guaranteed no future events for that window.
``min(W_upstream)`` is the strongest such guarantee: the slowest
upstream is the bottleneck. If one upstream is lagging at watermark
T-30s while another is at T, the downstream cannot close T-15s
windows yet — the lagging upstream might still emit a T-25s event.

Monotonicity
------------
The Dataflow Model requires that watermarks NEVER move backward
within a single source (Akidau et al. § 3.2). This tracker
enforces monotonicity per upstream — a ``receive(upstream, ts)``
call with ts < previously-received timestamp for that upstream is
silently clamped to the previous max, with a warning. This is
defensive: the upstream is supposed to send monotone watermarks,
but network reordering / process restarts can violate that, and the
tracker must not produce an incorrect composite watermark on bad input.

Composite watermark is then monotone by construction: if every input
is monotone, ``min`` of monotones is monotone (in NumPy terms, take
the running maximum of the per-input watermark stream, then take min
across the latest per-input values).
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

logger = logging.getLogger("aura.streaming.watermark_tracker")


# Event-time NEG_INF: a watermark of "nothing observed yet" for an
# upstream that hasn't sent its first watermark. Composite watermark
# is NEG_INF until every upstream has emitted at least one watermark
# (matches Dataflow Model behaviour: a downstream with one silent
# upstream cannot close any window).
NEG_INF = float("-inf")


class WatermarkTracker:
    """Aggregate N upstream watermarks into ``W_composite = min(...)``.

    Thread-safe. Multiple operator coroutines can call ``receive``
    concurrently without coordinating; the internal lock serialises
    state updates.

    Usage::

        tracker = WatermarkTracker(upstream_ids=["src_a", "src_b"])

        # Each upstream periodically sends its watermark:
        tracker.receive("src_a", current_a_watermark)
        tracker.receive("src_b", current_b_watermark)

        # Window operators read composite for trigger evaluation:
        if tracker.composite >= window.end_ts:
            fire_window(window)
    """

    def __init__(self, upstream_ids: List[str]) -> None:
        if not upstream_ids:
            raise ValueError("WatermarkTracker requires at least one upstream_id")
        if len(set(upstream_ids)) != len(upstream_ids):
            raise ValueError(f"upstream_ids must be unique: {upstream_ids}")
        # Per-upstream latest-known watermark. NEG_INF means "not yet
        # observed any watermark from this upstream"; composite stays
        # at NEG_INF as long as ANY upstream is still NEG_INF.
        self._per_input: Dict[str, float] = {u: NEG_INF for u in upstream_ids}
        # Cached composite — recomputed lazily but the recomputation is
        # O(N upstreams) so even for large N (rarely > 10) it's cheap.
        self._lock = threading.Lock()
        self._upstream_order = list(upstream_ids)

    # ── Public API ────────────────────────────────────────────────────

    def receive(self, upstream_id: str, watermark_ts: float) -> None:
        """Record a new watermark from ``upstream_id``.

        Per Dataflow Model § 3.2, watermarks are monotone within a
        single source. A ``watermark_ts`` smaller than the previously-
        received value for the same upstream is silently clamped to
        the previous max (with a logged warning) — the tracker must
        not let a misbehaving upstream pull the composite watermark
        backward.

        ``NaN`` is rejected (raises ValueError). ``-inf`` is allowed
        as a sentinel for "no watermark yet."
        """
        if watermark_ts != watermark_ts:  # NaN check
            raise ValueError(f"watermark_ts is NaN for upstream {upstream_id!r}")
        with self._lock:
            if upstream_id not in self._per_input:
                raise KeyError(
                    f"unknown upstream {upstream_id!r}; "
                    f"expected one of {list(self._per_input)}"
                )
            prev = self._per_input[upstream_id]
            if watermark_ts < prev:
                logger.warning(
                    "non-monotone watermark from %s: %r → %r (clamped to %r)",
                    upstream_id, prev, watermark_ts, prev,
                )
                return
            self._per_input[upstream_id] = watermark_ts

    @property
    def composite(self) -> float:
        """Current composite watermark = ``min(per-upstream watermarks)``.

        Returns ``-inf`` until every upstream has reported at least
        one watermark. This is the strongest correctness signal a
        downstream window can wait on per the Dataflow Model."""
        with self._lock:
            return min(self._per_input.values())

    @property
    def per_input(self) -> Dict[str, float]:
        """Read-only snapshot of the latest watermark per upstream.
        Useful for diagnosing 'which upstream is the bottleneck?'"""
        with self._lock:
            return dict(self._per_input)

    @property
    def slowest_upstream(self) -> Optional[str]:
        """ID of the upstream currently holding the composite back.
        Returns None when the tracker has no observations yet."""
        with self._lock:
            if all(v == NEG_INF for v in self._per_input.values()):
                return None
            return min(self._per_input.items(), key=lambda kv: kv[1])[0]

    def lag(self) -> Dict[str, float]:
        """Per-upstream lag relative to the FASTEST upstream.

        ``lag[upstream] = max(per_input) - per_input[upstream]``. A
        large lag indicates a backpressure or skew problem — operators
        can use this to prioritise inflow from the lagging source."""
        with self._lock:
            if all(v == NEG_INF for v in self._per_input.values()):
                return {u: 0.0 for u in self._upstream_order}
            fastest = max(self._per_input.values())
            return {u: fastest - v for u, v in self._per_input.items()}


__all__ = ["WatermarkTracker", "NEG_INF"]
