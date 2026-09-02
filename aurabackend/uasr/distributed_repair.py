"""UASR Distributed Repair Coordinator — fleet-wide recovery admission.

The in-process :class:`~uasr.repair_scheduler.RepairScheduler` bounds repair
concurrency *within one worker process*.  In a multi-node deployment that bound
leaks: N nodes each admit ``max_concurrent`` repairs, so the *shared* backend
(the LLM shim-synthesis API, the sandbox validation pool) sees ``N ×
max_concurrent`` load, and there is no cross-node ordering — a LOW-severity
repair on node A runs while a CRITICAL repair on node B waits.

``DistributedRepairCoordinator`` lifts admission control to the fleet using
Redis as the coordination substrate:

* **Global bounded concurrency** — at most ``max_global_concurrent`` repairs run
  across *all* nodes at once, so the shared backend load is capped regardless of
  fleet size.
* **Global priority ordering** — a Redis sorted-set wait queue scored by
  (severity, arrival) admits CRITICAL repairs before LOW ones *across nodes*.
* **FIFO fairness within a severity** — a global ``INCR`` sequence breaks ties in
  arrival order, fleet-wide.
* **Fault tolerance** — every admission is a *lease* with a TTL held in a second
  sorted set.  A node that crashes mid-repair stops heartbeating; its lease
  expires and the slot is reclaimed automatically, so a dead node cannot
  permanently consume a global slot.
* **Per-source fairness (opt-in, fleet-wide)** — ``max_per_source`` mirrors
  ``RepairScheduler``'s local cap across the whole fleet: a saturated
  source's queued entries are skipped (not discarded) when a slot frees, so
  one noisy source can't starve every other source's recovery budget
  cluster-wide either. A Redis hash tracks live-lease counts per source,
  decremented on release and on crash-lease reclamation alike.

Design notes
------------
The admit decision (prune expired leases → check the global count → claim the
head of the queue) must be atomic across nodes.  Redis Lua ``EVAL`` would do
this in one round trip, but is not available on every deployment / test double,
so the critical section is guarded by a short-lived Redis lock
(``SET NX PX``).  The section is a handful of O(log n) sorted-set ops
(sub-millisecond), and repairs are expensive (seconds), so lock contention is
negligible; the lock's PX TTL is a crash backstop.

``redis`` is an optional dependency (injected client for tests), mirroring
:class:`~uasr.state_store.RedisStateStore`.  The coordinator's public surface
(``submit``) matches ``RepairScheduler`` so a worker can route through either.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .models import DriftSeverity

# Severity → numeric rank (higher = more urgent → admitted first).
_SEVERITY_RANK: Dict[DriftSeverity, int] = {
    DriftSeverity.CRITICAL: 3,
    DriftSeverity.HIGH: 2,
    DriftSeverity.MEDIUM: 1,
    DriftSeverity.LOW: 0,
}
_RANK_BASE = 10          # > max rank; used to invert rank into a min-first score
_SEQ_SPACE = 10 ** 13    # room for the monotonic sequence below each rank band


@dataclass
class CoordinatorStats:
    submitted: int = 0
    admitted: int = 0
    completed: int = 0
    failed: int = 0
    reclaimed_leases: int = 0
    max_observed_global: int = 0
    per_severity_admitted: Dict[str, int] = field(default_factory=dict)
    per_severity_wait_ms: Dict[str, List[float]] = field(default_factory=dict)

    def wait_summary(self) -> Dict[str, float]:
        return {
            sev: round(sum(v) / len(v), 3) if v else 0.0
            for sev, v in self.per_severity_wait_ms.items()
        }


class DistributedRepairCoordinator:
    """Fleet-wide, Redis-backed, priority-ordered repair admission gate.

    Usage (per node)::

        coord = DistributedRepairCoordinator(client=redis_client,
                                             max_global_concurrent=8)
        result = await coord.submit(source_id, severity, coro_factory)

    ``submit`` blocks (cooperatively) until a global slot is free *and* this
    repair is at the head of the fleet-wide priority queue, then runs the repair
    while heartbeating its lease, and finally releases the slot.
    """

    def __init__(
        self,
        client: Any,
        max_global_concurrent: int = 8,
        max_per_source: int = 0,
        namespace: str = "uasr:repair",
        lease_ms: int = 30_000,
        heartbeat_ms: int = 5_000,
        poll_ms: int = 25,
        node_id: Optional[str] = None,
    ) -> None:
        if max_global_concurrent < 1:
            raise ValueError("max_global_concurrent must be >= 1")
        if max_per_source < 0:
            raise ValueError("max_per_source must be >= 0 (0 = unlimited)")
        if heartbeat_ms >= lease_ms:
            raise ValueError("heartbeat_ms must be < lease_ms so leases stay live")
        self._r = client
        self._max = max_global_concurrent
        # Per-source fairness (fleet-wide): mirrors RepairScheduler's local
        # max_per_source (uasr/repair_scheduler.py) so one noisy source can't
        # occupy every global slot across the whole fleet either. 0 (default)
        # preserves the original global-only bound.
        self._max_per_source = max_per_source
        self._ns = namespace
        self._lease_ms = lease_ms
        self._heartbeat_ms = heartbeat_ms
        self._poll = poll_ms / 1000.0
        self._node = node_id or uuid.uuid4().hex[:12]
        self.stats = CoordinatorStats()

    # ---- key helpers ------------------------------------------------
    @property
    def _waitq(self) -> str:
        return f"{self._ns}:waitq"

    @property
    def _active(self) -> str:
        return f"{self._ns}:active"

    @property
    def _seqkey(self) -> str:
        return f"{self._ns}:seq"

    @property
    def _lockkey(self) -> str:
        return f"{self._ns}:admit_lock"

    @property
    def _active_by_source_key(self) -> str:
        return f"{self._ns}:active_by_source"

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _decode(val: Any) -> str:
        return val.decode("utf-8") if isinstance(val, bytes) else val

    def _source_of(self, token: str) -> str:
        # token = f"{node}:{seq}:{source_id}" -- split with maxsplit=2 so a
        # source_id containing ':' is preserved intact.
        return token.split(":", 2)[2]

    # ---- observability ---------------------------------------------
    # Every method below does real synchronous redis-py network I/O
    # (``self._r.*``). The deployment runs one uvicorn worker, so each call
    # is wrapped in ``asyncio.to_thread`` -- otherwise a single admission
    # poll or heartbeat would stall every concurrent tenant's request for
    # the round-trip's duration (see .claude/rules/backend.md). This makes
    # every method here a coroutine; callers (including the tests) must
    # ``await`` them.
    async def active_count(self) -> int:
        """Global count of live (non-expired) leases across the fleet."""
        await self._prune_expired()
        return int(await asyncio.to_thread(self._r.zcard, self._active))

    async def queue_depth(self) -> int:
        return int(await asyncio.to_thread(self._r.zcard, self._waitq))

    async def active_count_for_source(self, source_id: str) -> int:
        """Fleet-wide count of live leases for one source_id."""
        val = await asyncio.to_thread(self._r.hget, self._active_by_source_key, source_id)
        return int(val) if val is not None else 0

    # ---- internal coordination -------------------------------------
    async def _prune_expired(self) -> int:
        """Reclaim leases whose TTL has passed (a crashed node's slots)."""
        now = self._now_ms()
        expired = await asyncio.to_thread(self._r.zrangebyscore, self._active, 0, now)
        if not expired:
            return 0
        for raw in expired:
            await asyncio.to_thread(
                self._r.hincrby, self._active_by_source_key, self._source_of(self._decode(raw)), -1
            )
        removed = int(await asyncio.to_thread(self._r.zremrangebyscore, self._active, 0, now))
        self.stats.reclaimed_leases += removed
        return removed

    def _score(self, severity: DriftSeverity, seq: int) -> float:
        rank = _SEVERITY_RANK.get(severity, 0)
        # lower score = admitted first: invert rank, then add arrival seq
        return (_RANK_BASE - rank) * _SEQ_SPACE + seq

    async def _enqueue(self, source_id: str, severity: DriftSeverity) -> str:
        seq = int(await asyncio.to_thread(self._r.incr, self._seqkey))
        token = f"{self._node}:{seq}:{source_id}"
        await asyncio.to_thread(self._r.zadd, self._waitq, {token: self._score(severity, seq)})
        return token

    async def _acquire_lock(self) -> Optional[str]:
        val = uuid.uuid4().hex
        ok = await asyncio.to_thread(self._r.set, self._lockkey, val, nx=True, px=2000)
        return val if ok else None

    async def _release_lock(self, val: str) -> None:
        # Best-effort compare-and-delete; the PX TTL is the crash backstop.
        cur = await asyncio.to_thread(self._r.get, self._lockkey)
        if cur is not None:
            if isinstance(cur, bytes):
                cur = cur.decode("utf-8")
            if cur == val:
                await asyncio.to_thread(self._r.delete, self._lockkey)

    async def _try_admit(self, token: str) -> bool:
        """Atomically admit ``token`` iff a global slot is free and it is the
        highest-priority fleet-wide queue entry not blocked by the
        per-source cap.  Returns True on admission.

        With ``max_per_source`` off (0), this is exactly "head of queue",
        unchanged. With it on, a source already at its cap is skipped when
        scanning for the next admittable entry -- same "skip, don't
        discard" fairness RepairScheduler uses locally
        (uasr/repair_scheduler.py) -- so a saturated source can't also
        block a different, uncapped source's turn.
        """
        lock = await self._acquire_lock()
        if lock is None:
            return False
        try:
            await self._prune_expired()
            if int(await asyncio.to_thread(self._r.zcard, self._active)) >= self._max:
                return False
            my_source = self._source_of(token)
            if self._max_per_source > 0:
                mine_active = int(
                    await asyncio.to_thread(self._r.hget, self._active_by_source_key, my_source) or 0
                )
                if mine_active >= self._max_per_source:
                    return False
            # Scan the queue in priority order for the first entry eligible
            # to run (its source under the per-source cap, or fairness off).
            # A small bound keeps this O(1)-ish under normal queue depths;
            # anything beyond it just means "not our turn yet" either way.
            queue = await asyncio.to_thread(self._r.zrange, self._waitq, 0, 199)
            for raw in queue:
                cand = self._decode(raw)
                if self._max_per_source > 0:
                    cand_source = self._source_of(cand)
                    cand_active = int(
                        await asyncio.to_thread(self._r.hget, self._active_by_source_key, cand_source) or 0
                    )
                    if cand_active >= self._max_per_source:
                        continue
                if cand != token:
                    return False
                # Claim: move token from wait queue into active leases.
                await asyncio.to_thread(self._r.zrem, self._waitq, token)
                await asyncio.to_thread(
                    self._r.zadd, self._active, {token: self._now_ms() + self._lease_ms}
                )
                await asyncio.to_thread(self._r.hincrby, self._active_by_source_key, my_source, 1)
                return True
            return False
        finally:
            await self._release_lock(lock)

    async def _heartbeat(self, token: str) -> None:
        await asyncio.to_thread(self._r.zadd, self._active, {token: self._now_ms() + self._lease_ms})

    async def _release(self, token: str) -> None:
        await asyncio.to_thread(self._r.zrem, self._active, token)
        await asyncio.to_thread(self._r.hincrby, self._active_by_source_key, self._source_of(token), -1)

    async def _heartbeat_loop(self, token: str) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_ms / 1000.0)
                await self._heartbeat(token)
        except asyncio.CancelledError:
            raise

    # ---- public API -------------------------------------------------
    async def submit(
        self,
        source_id: str,
        severity: DriftSeverity,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Enqueue a repair, wait for a fleet-wide slot in priority order, run
        it under a heartbeated lease, and release the slot.  Returns the
        repair's result (or re-raises its exception)."""
        self.stats.submitted += 1
        enqueued_at = time.perf_counter()
        token = await self._enqueue(source_id, severity)

        # Cooperative wait until admitted.
        while not await self._try_admit(token):
            await asyncio.sleep(self._poll)

        wait_ms = (time.perf_counter() - enqueued_at) * 1000.0
        sev = severity.value
        self.stats.admitted += 1
        self.stats.per_severity_admitted[sev] = self.stats.per_severity_admitted.get(sev, 0) + 1
        self.stats.per_severity_wait_ms.setdefault(sev, []).append(wait_ms)
        self.stats.max_observed_global = max(
            self.stats.max_observed_global, int(await asyncio.to_thread(self._r.zcard, self._active))
        )

        hb = asyncio.create_task(self._heartbeat_loop(token))
        try:
            result = await coro_factory()
            self.stats.completed += 1
            return result
        except Exception:
            self.stats.failed += 1
            raise
        finally:
            hb.cancel()
            try:
                await hb
            except asyncio.CancelledError:
                pass
            await self._release(token)
