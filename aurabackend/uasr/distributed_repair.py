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
        namespace: str = "uasr:repair",
        lease_ms: int = 30_000,
        heartbeat_ms: int = 5_000,
        poll_ms: int = 25,
        node_id: Optional[str] = None,
    ) -> None:
        if max_global_concurrent < 1:
            raise ValueError("max_global_concurrent must be >= 1")
        if heartbeat_ms >= lease_ms:
            raise ValueError("heartbeat_ms must be < lease_ms so leases stay live")
        self._r = client
        self._max = max_global_concurrent
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

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    # ---- observability ---------------------------------------------
    def active_count(self) -> int:
        """Global count of live (non-expired) leases across the fleet."""
        self._prune_expired()
        return int(self._r.zcard(self._active))

    def queue_depth(self) -> int:
        return int(self._r.zcard(self._waitq))

    # ---- internal coordination -------------------------------------
    def _prune_expired(self) -> int:
        """Reclaim leases whose TTL has passed (a crashed node's slots)."""
        removed = int(self._r.zremrangebyscore(self._active, 0, self._now_ms()))
        if removed:
            self.stats.reclaimed_leases += removed
        return removed

    def _score(self, severity: DriftSeverity, seq: int) -> float:
        rank = _SEVERITY_RANK.get(severity, 0)
        # lower score = admitted first: invert rank, then add arrival seq
        return (_RANK_BASE - rank) * _SEQ_SPACE + seq

    def _enqueue(self, source_id: str, severity: DriftSeverity) -> str:
        seq = int(self._r.incr(self._seqkey))
        token = f"{self._node}:{seq}:{source_id}"
        self._r.zadd(self._waitq, {token: self._score(severity, seq)})
        return token

    def _acquire_lock(self) -> Optional[str]:
        val = uuid.uuid4().hex
        ok = self._r.set(self._lockkey, val, nx=True, px=2000)
        return val if ok else None

    def _release_lock(self, val: str) -> None:
        # Best-effort compare-and-delete; the PX TTL is the crash backstop.
        cur = self._r.get(self._lockkey)
        if cur is not None:
            if isinstance(cur, bytes):
                cur = cur.decode("utf-8")
            if cur == val:
                self._r.delete(self._lockkey)

    def _try_admit(self, token: str) -> bool:
        """Atomically admit ``token`` iff a global slot is free and it is the
        head of the fleet-wide priority queue.  Returns True on admission."""
        lock = self._acquire_lock()
        if lock is None:
            return False
        try:
            self._prune_expired()
            if int(self._r.zcard(self._active)) >= self._max:
                return False
            head = self._r.zrange(self._waitq, 0, 0)
            if not head:
                return False
            head_tok = head[0].decode("utf-8") if isinstance(head[0], bytes) else head[0]
            if head_tok != token:
                return False
            # Claim: move token from wait queue into active leases.
            self._r.zrem(self._waitq, token)
            self._r.zadd(self._active, {token: self._now_ms() + self._lease_ms})
            return True
        finally:
            self._release_lock(lock)

    def _heartbeat(self, token: str) -> None:
        self._r.zadd(self._active, {token: self._now_ms() + self._lease_ms})

    def _release(self, token: str) -> None:
        self._r.zrem(self._active, token)

    async def _heartbeat_loop(self, token: str) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_ms / 1000.0)
                self._heartbeat(token)
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
        token = self._enqueue(source_id, severity)

        # Cooperative wait until admitted.
        while not self._try_admit(token):
            await asyncio.sleep(self._poll)

        wait_ms = (time.perf_counter() - enqueued_at) * 1000.0
        sev = severity.value
        self.stats.admitted += 1
        self.stats.per_severity_admitted[sev] = self.stats.per_severity_admitted.get(sev, 0) + 1
        self.stats.per_severity_wait_ms.setdefault(sev, []).append(wait_ms)
        self.stats.max_observed_global = max(
            self.stats.max_observed_global, int(self._r.zcard(self._active))
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
            self._release(token)
