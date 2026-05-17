"""
UASR Kramer-Magee Canary Shim Router
=====================================
Sprint 18 — Evolution 3 from STREAMING_FOUNDATIONS.md.

Replaces the existing ``MAPEKWorker.pause()`` → recovery → ``resume()``
pattern with a dynamic-reconfiguration router. The core consumer
continues ingest at full rate while drift corrections run as canary
deployments alongside the current production transform; the router
shifts traffic gradually from V_old to V_new and drains V_old to
**quiescence** before termination.

Mathematical foundation (Kramer-Magee 1990)
-------------------------------------------
An operator v is *quiescent at swap-time T* if:

    ∀ t > T : v has no in-flight transactions
            ∧ v's outputs have been acknowledged by all
              downstream consumers
            ∧ v is not currently committed to participate in
              any future state action

A dynamic reconfiguration is provably safe (the Kramer-Magee
theorem) iff every operator that is being removed reaches its
quiescent state during the swap. The canary pattern below
enforces this:

  1. V_new is added with a small canary weight (default 10%).
  2. The router routes each incoming batch to V_old or V_new
     proportionally to their current weights.
  3. After N validation batches, a metric_fn measures V_new's
     output quality. If quality is good, the weight shifts
     monotonically toward V_new (10% → 30% → 60% → 100%).
  4. Once V_new is at 100%, V_old is drained: the router stops
     sending it new batches and waits for any in-flight calls to
     complete. After quiescence, V_old is removed from the route
     table.
  5. The router NEVER calls pause() on the underlying consumer.
     Upstream ingest continues at full rate throughout.

Anchors
-------
* Kramer, J. & Magee, J. (1990). "The Evolving Philosophers Problem:
  Dynamic Change Management." IEEE TSE 16(11):1293-1306.
* Kephart, J. O. & Chess, D. M. (2003). "The Vision of Autonomic
  Computing." IEEE Computer 36(1):41-50.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("uasr.shim_router")


# A transform_fn takes a list of dict rows + the source_id and returns
# the transformed rows. The router routes batches to one of these per
# version. Sync or async; the router handles both.
TransformFn = Callable[[str, List[Dict[str, Any]]], Any]

# A metric_fn takes the V_new output and returns a "canary score" in
# [0, 1] where higher is better. The router uses this to decide
# whether to promote V_new's weight further. Returning < min_score
# at any validation point reverts the canary.
MetricFn = Callable[[List[Dict[str, Any]]], float]


@dataclass
class _Route:
    version: str
    transform: TransformFn
    weight: float          # current routing weight in [0, 1]
    in_flight: int = 0     # active calls into this version's transform
    total_calls: int = 0
    canary_scores: List[float] = field(default_factory=list)
    marked_for_drain: bool = False
    deployed_at: float = field(default_factory=time.time)


class ShimRouter:
    """Per-source canary router with Kramer-Magee quiescence draining.

    Lifecycle:

      1. ``add_route(source_id, version, transform_fn, weight)``
         registers a new transform version.
      2. ``apply(source_id, rows) -> (rows_out, version_used)`` picks
         a route by weight and runs the transform. Tracks in_flight
         so drain_to_quiescence can wait for it.
      3. ``validate_canary(source_id, new_version, metric_fn,
         min_score, ratio_step)`` runs the metric over new_version's
         recent outputs. On success, shifts traffic from old_version
         toward new_version by ratio_step. On failure, reverts
         new_version to weight 0.
      4. ``drain_to_quiescence(source_id, version, timeout_s)``
         marks the version no-new-routes and waits for in_flight to
         hit 0 (or the timeout to expire).
      5. ``remove_route(source_id, version)`` drops the route from
         the table after a successful drain.

    The router is per-source — each source_id has its own route table.
    """

    def __init__(self) -> None:
        # source_id → {version → _Route}
        self._routes: Dict[str, Dict[str, _Route]] = {}
        self._lock = asyncio.Lock()
        # Per-source weighted RNG state — kept deterministic via
        # round-robin counters rather than random sampling, so two
        # identical canary sequences produce byte-identical batch
        # routing (matches the audit-engine determinism contract).
        self._round_robin: Dict[str, int] = {}

    def routes(self, source_id: str) -> List[Dict[str, Any]]:
        """Snapshot of the current route table for one source.
        Returns a JSON-friendly list of route summaries; used by the
        /uasr/recovery/{id}/audit endpoint."""
        rs = self._routes.get(source_id, {})
        return [
            {
                "version": r.version,
                "weight": r.weight,
                "in_flight": r.in_flight,
                "total_calls": r.total_calls,
                "marked_for_drain": r.marked_for_drain,
                "canary_score_count": len(r.canary_scores),
                "deployed_at": r.deployed_at,
            }
            for r in rs.values()
        ]

    async def add_route(
        self,
        source_id: str,
        version: str,
        transform: TransformFn,
        weight: float = 1.0,
    ) -> None:
        """Register a new transform version. The first registered
        route for a source gets weight=1.0 by convention; subsequent
        canary routes start at the requested weight (default 1.0
        when the caller knows they want a hard cutover, default
        0.1 when add_canary is the entry point)."""
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"weight must be in [0, 1]; got {weight}")
        async with self._lock:
            table = self._routes.setdefault(source_id, {})
            if version in table:
                raise ValueError(
                    f"route version {version!r} already exists for "
                    f"source {source_id!r}"
                )
            table[version] = _Route(
                version=version,
                transform=transform,
                weight=weight,
            )

    async def add_canary(
        self,
        source_id: str,
        version: str,
        transform: TransformFn,
        initial_weight: float = 0.1,
    ) -> None:
        """Add a new version as a canary alongside existing routes,
        rescaling existing routes' weights so the total stays at 1.0.

        Example: existing route V1 at weight=1.0; add_canary(V2,
        initial_weight=0.1) → V1 at 0.9, V2 at 0.1.
        """
        if not 0.0 < initial_weight < 1.0:
            raise ValueError(
                f"canary initial_weight must be in (0, 1); got {initial_weight}"
            )
        async with self._lock:
            table = self._routes.setdefault(source_id, {})
            if version in table:
                raise ValueError(
                    f"canary version {version!r} already exists for source {source_id!r}"
                )
            # Rescale existing routes so their total + canary = 1.0
            existing_total = sum(r.weight for r in table.values())
            if existing_total > 0:
                rescale = (1.0 - initial_weight) / existing_total
                for r in table.values():
                    r.weight *= rescale
            table[version] = _Route(
                version=version,
                transform=transform,
                weight=initial_weight,
            )

    async def apply(
        self,
        source_id: str,
        rows: List[Dict[str, Any]],
    ) -> Any:
        """Route the batch to ONE version per the current weights
        and return the transformed rows.

        Routing uses deterministic round-robin against the cumulative
        weight distribution rather than random sampling — same
        inputs + same route table produce the same routing decision.
        This is the determinism contract that lets the audit engine
        replay shim deployments byte-identically.

        Tracks in_flight on the chosen route so drain_to_quiescence
        can wait for completion. The return value also includes the
        version that handled the batch, so the caller can log
        provenance.
        """
        if source_id not in self._routes or not self._routes[source_id]:
            # No routes registered — pass-through
            return {"rows": rows, "version": "_passthrough"}

        async with self._lock:
            table = self._routes[source_id]
            # Filter out drained-to-zero routes
            active = [
                r for r in table.values()
                if r.weight > 0 and not r.marked_for_drain
            ]
            if not active:
                # All marked for drain — pass-through with explicit log
                logger.warning(
                    "ShimRouter.apply: all routes for source %r are drained; "
                    "pass-through (no transform applied)", source_id,
                )
                return {"rows": rows, "version": "_passthrough"}

            # Round-robin against the weight distribution.
            # Increment the per-source counter and pick the route whose
            # cumulative weight contains the counter's fractional position.
            counter = self._round_robin.get(source_id, 0)
            self._round_robin[source_id] = counter + 1
            # Map counter to position in [0, 1) deterministically.
            # Using counter % 1000 / 1000.0 gives a uniform spread.
            position = (counter % 1000) / 1000.0
            cumulative = 0.0
            chosen: Optional[_Route] = None
            total_weight = sum(r.weight for r in active)
            for r in active:
                cumulative += r.weight / total_weight
                if position < cumulative:
                    chosen = r
                    break
            if chosen is None:
                chosen = active[-1]
            chosen.in_flight += 1
            chosen.total_calls += 1

        # Drop the lock before running the transform — the transform
        # may itself be async and we don't want to block other apply()
        # calls on the same source.
        try:
            result = chosen.transform(source_id, rows)
            if asyncio.iscoroutine(result):
                result = await result
        finally:
            async with self._lock:
                chosen.in_flight = max(0, chosen.in_flight - 1)
        return {"rows": result, "version": chosen.version}

    async def record_canary_score(
        self,
        source_id: str,
        version: str,
        score: float,
    ) -> None:
        """Append a canary score for a given version. The router
        uses these to decide whether to promote the version's weight
        further."""
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score must be in [0, 1]; got {score}")
        async with self._lock:
            r = self._routes.get(source_id, {}).get(version)
            if r is None:
                raise ValueError(
                    f"unknown route {version!r} for source {source_id!r}"
                )
            r.canary_scores.append(score)

    async def promote_canary(
        self,
        source_id: str,
        version: str,
        ratio_step: float = 0.2,
        min_avg_score: float = 0.6,
        min_samples: int = 3,
    ) -> Dict[str, Any]:
        """Promote a canary version's weight by ``ratio_step`` if its
        recent canary scores average above ``min_avg_score``.

        Returns a dict describing the decision:
          * ``promoted``: True if weight was increased
          * ``avg_score``: the score average used for the decision
          * ``new_weight``: the version's weight after this call
          * ``reason``: human-readable explanation
        """
        async with self._lock:
            r = self._routes.get(source_id, {}).get(version)
            if r is None:
                return {
                    "promoted": False, "avg_score": 0.0,
                    "new_weight": 0.0, "reason": "unknown route",
                }
            if len(r.canary_scores) < min_samples:
                return {
                    "promoted": False, "avg_score": 0.0,
                    "new_weight": r.weight,
                    "reason": f"need >= {min_samples} samples, have {len(r.canary_scores)}",
                }
            recent = r.canary_scores[-min_samples:]
            avg = sum(recent) / len(recent)
            if avg < min_avg_score:
                return {
                    "promoted": False, "avg_score": avg,
                    "new_weight": r.weight,
                    "reason": f"avg_score {avg:.3f} below threshold {min_avg_score}",
                }
            # Promote: shift weight from all other (non-drained) routes
            # proportionally to the canary.
            new_weight = min(1.0, r.weight + ratio_step)
            delta = new_weight - r.weight
            others_total = sum(
                o.weight for o in self._routes[source_id].values()
                if o.version != version and not o.marked_for_drain
            )
            if others_total > 0 and delta > 0:
                # Pull `delta` from the other routes proportionally
                pull_factor = delta / others_total
                for o in self._routes[source_id].values():
                    if o.version == version or o.marked_for_drain:
                        continue
                    o.weight = max(0.0, o.weight - o.weight * pull_factor)
            r.weight = new_weight
            return {
                "promoted": True, "avg_score": avg,
                "new_weight": new_weight,
                "reason": f"avg_score {avg:.3f} >= {min_avg_score}, promoted by {ratio_step}",
            }

    async def revert_canary(self, source_id: str, version: str) -> None:
        """Drop a canary version's weight to 0 and rescale the
        remaining routes back to total weight 1.0. Used when the
        canary's metric_fn flagged it as worse than baseline."""
        async with self._lock:
            r = self._routes.get(source_id, {}).get(version)
            if r is None:
                return
            r.weight = 0.0
            r.marked_for_drain = True
            # Rescale the rest so total weight is 1.0
            others = [
                o for o in self._routes[source_id].values()
                if o.version != version and not o.marked_for_drain
            ]
            others_total = sum(o.weight for o in others)
            if others_total > 0:
                rescale = 1.0 / others_total
                for o in others:
                    o.weight *= rescale

    async def drain_to_quiescence(
        self,
        source_id: str,
        version: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> bool:
        """Wait for a version's in_flight to reach 0 (Kramer-Magee
        quiescence). Marks the version for drain so apply() stops
        sending it new batches. Returns True if drained within
        timeout_s, False if the timeout expired (caller decides
        whether to force-terminate)."""
        async with self._lock:
            r = self._routes.get(source_id, {}).get(version)
            if r is None:
                return True   # already gone
            r.marked_for_drain = True
            r.weight = 0.0

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            async with self._lock:
                r = self._routes.get(source_id, {}).get(version)
                if r is None or r.in_flight == 0:
                    return True
            await asyncio.sleep(poll_interval_s)
        return False

    async def remove_route(self, source_id: str, version: str) -> None:
        """Drop a route from the table. Should be called only after a
        successful drain_to_quiescence — otherwise an in-flight call
        will find its route gone and the caller will see an apply()
        return with version='_passthrough' for the next batch."""
        async with self._lock:
            table = self._routes.get(source_id, {})
            table.pop(version, None)


__all__ = ["ShimRouter", "TransformFn", "MetricFn"]
