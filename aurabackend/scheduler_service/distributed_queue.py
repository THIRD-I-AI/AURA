"""
Distributed-scheduler primitives — Sprint 20b (Pillar 4 continuation).

What this module ships
----------------------
Standalone primitives — NOT YET wired into `worker.py`. Sprint 20b ships
algorithmic correctness; integration is the S20.2 follow-up.

  * ``compute_lock_id(name)``         — deterministic SHA-256-derived
                                        Postgres advisory-lock bigint key.
  * ``NotifyPayload``                 — frozen dataclass + canonical
                                        JSON encode/decode for LISTEN/NOTIFY
                                        wire format.
  * ``ExponentialBackoff``            — async-safe backoff state machine.
  * ``DistributedQueue``              — async generator wrapping Postgres
                                        LISTEN that yields decoded payloads.
  * ``AdvisoryLockHolder``            — async context manager wrapping
                                        ``pg_try_advisory_lock`` with a
                                        timeout-poll loop and clean release.

Anchors:
  * PostgreSQL 16 Manual § 9.27.2 (Advisory Locks) — application-level
    locks identified by 64-bit bigint keys; namespaced by transaction
    or session; release is guaranteed on connection close.
    https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS
  * PostgreSQL 16 Manual § 47.11 (LISTEN/NOTIFY) — asynchronous server-
    push notification channel; payload up to 8KB; subscribers receive
    NOTIFY events on the next LISTEN poll.
    https://www.postgresql.org/docs/16/sql-listen.html
  * Lamport (1986) "The Mutual Exclusion Problem" CACM 29(1) — the
    distributed-leader-election problem class advisory-lock-based
    election solves; not implemented as bakery algorithm here because
    Postgres provides the underlying primitive.

Why these primitives instead of Redis / etcd?
---------------------------------------------
The scheduler ALREADY uses Postgres as the durable queue (the
``ScheduledJob`` table is in Postgres, not in-process). Adding a
separate coordination service (Redis for pub-sub, etcd for leader
election) would multiply operational surface area for no functional
gain — Postgres already provides:

  * Durable queue:        ScheduledJob table.
  * Async notification:   LISTEN/NOTIFY (replaces 60s polling).
  * Distributed locks:    pg_advisory_lock (replaces single-worker
                          assumption).
  * Atomic claim:         SELECT FOR UPDATE SKIP LOCKED on the job row.

This module wraps the four primitives in a stable Python API that
hides Postgres-specific SQL behind async helpers.

Determinism contracts
---------------------
* ``compute_lock_id`` is deterministic: same name → same bigint key,
  forever. Two scheduler replicas computing the lock for the same
  cron-evaluator critical section will contend on the same key.
* ``NotifyPayload`` canonical encoding produces byte-identical output
  for the same input — replaying a NOTIFY stream produces byte-identical
  recovery state. Useful for Layer 10 audit integration in S20.2.
* ``ExponentialBackoff.next_delay()`` is deterministic when ``jitter=0.0``.
  With jitter > 0, the controller uses ``random.uniform`` — caller seeds
  ``random`` if Layer 10 byte-identity is required for replay.

Standalone usage
----------------
The module is importable without an active Postgres connection. The
async helpers raise ``RuntimeError`` if invoked against a non-Postgres
SQLAlchemy engine (the underlying SQL is Postgres-specific). Pure-
Python helpers (compute_lock_id, NotifyPayload, ExponentialBackoff)
have ZERO database dependency and ship their own contract tests on
the base backend CI lane; the Postgres-gated helpers
(DistributedQueue, AdvisoryLockHolder) test in the new
``scheduler-distributed-test`` CI lane with a real Postgres service
container.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger("aura.scheduler.distributed_queue")


# ── Pure-Python primitives (no database dependency) ──────────────────


# Postgres advisory-lock keys are bigint (signed 64-bit). We derive a
# stable key from a name string by SHA-256 → take the first 8 bytes →
# interpret as signed 64-bit big-endian. The collision probability for
# N names is N²/2⁶⁵ — for N=10⁹ scheduler-critical-section names,
# collision probability ≈ 2.7×10⁻⁴. Acceptable for application-level
# advisory locks where a collision means two unrelated critical
# sections serialise unnecessarily (correctness preserved, throughput
# slightly reduced).
_BIGINT_MIN = -(1 << 63)
_BIGINT_MAX = (1 << 63) - 1


def compute_lock_id(name: str) -> int:
    """Derive a deterministic 64-bit bigint advisory-lock ID from a name.

    Two scheduler replicas calling ``compute_lock_id("cron_evaluator")``
    produce the SAME bigint, so their ``pg_advisory_lock`` calls
    contend on the same key — exactly what leader election requires.

    The derivation is:
        1. SHA-256(name UTF-8) — collision-resistant cryptographic hash.
        2. Take first 8 bytes — uniform random over the 64-bit space.
        3. Interpret as signed big-endian — Postgres bigint range.

    Args:
        name: Stable identifier for the critical section. Use
            descriptive names like ``"cron_evaluator"`` or
            ``"job_dispatch_dag_X"`` so log diagnostics are readable.
            Empty string is allowed but maps to a fixed bigint that
            multiple callers would unintentionally share.

    Returns:
        Signed 64-bit integer suitable for ``pg_advisory_lock``.
    """
    if not isinstance(name, str):
        raise TypeError(f"name must be str, got {type(name).__name__}")
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:8]
    key = int.from_bytes(digest, byteorder="big", signed=True)
    # Defensive: Postgres bigint range should always be satisfied by
    # signed 64-bit interpretation, but assert explicitly so a future
    # change to the derivation can't silently produce out-of-range keys.
    if not _BIGINT_MIN <= key <= _BIGINT_MAX:
        raise ValueError(f"derived key {key} outside bigint range")
    return key


@dataclass(frozen=True)
class NotifyPayload:
    """Wire format for LISTEN/NOTIFY events.

    Postgres NOTIFY payloads are strings up to ~8KB. We use canonical
    JSON encoding so two scheduler replicas decode the same bytes the
    same way — important for Layer 10 audit-engine integration in S20.2.

    Fields:
        kind: Event type. Drives subscriber dispatch — e.g.
            ``"job_inserted"``, ``"job_state_changed"``, ``"leader_lost"``.
        job_id: The ``ScheduledJob.id`` the event refers to. Empty
            string for events with no specific job (leader_lost,
            tick).
        payload: Arbitrary additional fields. Must be canonical-JSON
            serialisable (no datetime, no custom objects — encode as
            ISO-8601 strings before constructing the payload).
    """

    kind: str
    job_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_canonical_json(self) -> str:
        """Encode to canonical JSON — ``sort_keys=True``, compact separators.

        Two callers passing equivalent payloads produce byte-identical
        output. ``json.dumps`` on a Python dict with sorted keys gives
        deterministic ordering even though Python dicts preserve
        insertion order — the explicit ``sort_keys=True`` enforces it.
        """
        body = {"kind": self.kind, "job_id": self.job_id, "payload": self.payload}
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_canonical_json(cls, raw: str) -> "NotifyPayload":
        """Decode from canonical JSON. Validates shape; raises ValueError
        on missing/malformed fields rather than silently accepting an
        empty payload — a subscriber processing malformed events would
        be a hard-to-diagnose bug."""
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError(f"NotifyPayload must decode to dict, got {type(body).__name__}")
        for key in ("kind", "job_id", "payload"):
            if key not in body:
                raise ValueError(f"NotifyPayload missing required field {key!r}")
        if not isinstance(body["kind"], str):
            raise ValueError(f"NotifyPayload.kind must be str, got {type(body['kind']).__name__}")
        if not isinstance(body["job_id"], str):
            raise ValueError(f"NotifyPayload.job_id must be str, got {type(body['job_id']).__name__}")
        if not isinstance(body["payload"], dict):
            raise ValueError(
                f"NotifyPayload.payload must be dict, got {type(body['payload']).__name__}"
            )
        return cls(kind=body["kind"], job_id=body["job_id"], payload=body["payload"])


class ExponentialBackoff:
    """Async-safe exponential-backoff state machine.

    Used by ``AdvisoryLockHolder`` when polling for an advisory-lock
    that's held by another replica: first poll after ``base_delay_s``,
    second after ``base_delay_s * factor``, etc., capped at ``max_delay_s``.

    Deterministic when ``jitter == 0.0``. With jitter, the actual
    delay is ``base * factor**n * uniform(1.0, 1.0 + jitter)`` —
    spreads contention across replicas so they don't all retry in
    lock-step (the "thundering herd" pattern).

    State is reset on ``reset()``; typically the caller resets after
    a successful acquisition.
    """

    def __init__(
        self,
        base_delay_s: float = 0.05,
        factor: float = 2.0,
        max_delay_s: float = 5.0,
        jitter: float = 0.1,
    ) -> None:
        if base_delay_s <= 0:
            raise ValueError(f"base_delay_s must be > 0, got {base_delay_s}")
        if factor < 1.0:
            raise ValueError(f"factor must be >= 1.0, got {factor}")
        if max_delay_s < base_delay_s:
            raise ValueError(
                f"max_delay_s ({max_delay_s}) must be >= base_delay_s ({base_delay_s})"
            )
        if not 0.0 <= jitter <= 1.0:
            raise ValueError(f"jitter must be in [0.0, 1.0], got {jitter}")
        self._base_delay_s = float(base_delay_s)
        self._factor = float(factor)
        self._max_delay_s = float(max_delay_s)
        self._jitter = float(jitter)
        self._step: int = 0

    def next_delay(self) -> float:
        """Compute and return the next delay; advance internal state.

        Determinism: with ``jitter == 0`` the output is purely a
        function of step count. With jitter, uses ``random.uniform`` —
        seed Python's random module if Layer 10 byte-identity is
        required.
        """
        base = min(self._base_delay_s * (self._factor ** self._step), self._max_delay_s)
        self._step += 1
        if self._jitter == 0.0:
            return base
        return base * random.uniform(1.0, 1.0 + self._jitter)

    def reset(self) -> None:
        """Reset to step 0. Caller invokes after successful acquisition
        so the next contention starts from the short base delay."""
        self._step = 0

    @property
    def step(self) -> int:
        """Number of next_delay() calls since construction or reset.
        Diagnostic — useful for 'how many retries did this acquisition
        take?' telemetry."""
        return self._step


# ── Postgres-dependent primitives ────────────────────────────────────


class AdvisoryLockHolder:
    """Async context manager wrapping Postgres ``pg_try_advisory_lock``
    with a timeout-poll loop and guaranteed session-scoped release.

    Usage::

        async with AdvisoryLockHolder(
            engine=scheduler_engine,
            lock_id=compute_lock_id("cron_evaluator"),
            timeout_s=10.0,
        ) as acquired:
            if acquired:
                # We are the elected leader for this critical section.
                await evaluate_cron_jobs()
            else:
                # Another replica holds the lock; back off and let them work.
                logger.debug("not the cron leader this cycle")

    The context manager ALWAYS releases the lock on exit (success
    OR exception OR timeout). On Postgres, advisory locks held in
    session scope auto-release on connection close — so even if our
    process dies mid-critical-section, the lock frees within seconds.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        lock_id: int,
        *,
        timeout_s: float = 5.0,
        backoff: Optional[ExponentialBackoff] = None,
    ) -> None:
        if not isinstance(lock_id, int):
            raise TypeError(f"lock_id must be int (Postgres bigint), got {type(lock_id).__name__}")
        if not _BIGINT_MIN <= lock_id <= _BIGINT_MAX:
            raise ValueError(f"lock_id {lock_id} outside Postgres bigint range")
        if timeout_s < 0:
            raise ValueError(f"timeout_s must be >= 0, got {timeout_s}")
        self._engine = engine
        self._lock_id = lock_id
        self._timeout_s = float(timeout_s)
        self._backoff = backoff or ExponentialBackoff()
        self._conn: Optional[AsyncConnection] = None
        self._acquired: bool = False

    async def __aenter__(self) -> bool:
        """Try to acquire the lock with the configured timeout.

        Returns True if acquired (caller IS the leader), False if
        timeout elapsed without acquisition (caller is NOT the leader).
        Never raises on contention — only on a real database error.
        """
        self._conn = await self._engine.connect()
        deadline = time.monotonic() + self._timeout_s
        while True:
            result = await self._conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": self._lock_id},
            )
            row = result.first()
            got = bool(row[0]) if row else False
            if got:
                self._acquired = True
                self._backoff.reset()
                return True
            if time.monotonic() >= deadline:
                # Timeout elapsed; we did NOT acquire. Release the
                # connection so a future call can try again.
                return False
            await asyncio.sleep(self._backoff.next_delay())

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self._acquired and self._conn is not None:
                await self._conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": self._lock_id},
                )
        finally:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
            self._acquired = False

    @property
    def acquired(self) -> bool:
        """True iff the lock is currently held by this holder. Useful
        inside the with-body for explicit predicates rather than
        relying on the truthiness of the as-clause."""
        return self._acquired


class DistributedQueue:
    """Async LISTEN/NOTIFY wrapper for cross-replica scheduler events.

    Subscribers call ``listen(channel)`` to receive an async generator
    yielding decoded ``NotifyPayload`` objects until cancelled.
    Producers call ``notify(channel, payload)`` to broadcast.

    Usage (subscriber)::

        queue = DistributedQueue(engine=scheduler_engine)
        async for event in queue.listen("scheduler_jobs"):
            if event.kind == "job_inserted":
                await wake_worker(event.job_id)

    Usage (producer)::

        await queue.notify(
            "scheduler_jobs",
            NotifyPayload(kind="job_inserted", job_id="...", payload={}),
        )
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def notify(self, channel: str, payload: NotifyPayload) -> None:
        """Broadcast a payload on ``channel``. Returns immediately —
        Postgres delivers asynchronously to all currently-LISTENing
        connections.

        ``channel`` must be a valid Postgres identifier (alphanumeric
        + underscore, starting with a letter or underscore). The
        canonical encoding of payload is automatically applied.

        Note: Postgres NOTIFY payload is capped at ~8000 bytes. Large
        payloads should be referenced by job_id and looked up in the
        ScheduledJob table by the subscriber — that's the durability
        win of the table-backed queue model.
        """
        _validate_channel(channel)
        encoded = payload.to_canonical_json()
        if len(encoded.encode("utf-8")) > 7900:
            # Conservative cap — Postgres lets you go to ~8000 but margin
            # for protocol overhead saves a surprise outage.
            raise ValueError(
                f"NotifyPayload canonical encoding is {len(encoded)} bytes; "
                f"must be < 7900. Use job_id reference for large payloads."
            )
        async with self._engine.begin() as conn:
            # Use the pg_notify(text, text) FUNCTION form, not the
            # NOTIFY utility statement. The bare statement is a
            # Postgres parser-level construct that does NOT support
            # parameter binding — `NOTIFY chan, $1` fails with
            # "syntax error at or near $1". pg_notify is a regular
            # function, so SQLAlchemy text() bound params work, and
            # the channel can also be bound (no identifier issue
            # since pg_notify takes channel as a TEXT arg, not an
            # identifier). _validate_channel above still applies as
            # belt-and-braces in case pg_notify ever changes semantics.
            await conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": channel, "payload": encoded},
            )

    async def listen(
        self,
        channel: str,
        *,
        poll_interval_s: float = 0.2,
    ) -> AsyncIterator[NotifyPayload]:
        """Async-generator subscriber loop.

        Yields decoded ``NotifyPayload`` objects until the caller's
        ``async for`` is cancelled. Polls the underlying async driver's
        notify queue at ``poll_interval_s`` — small enough that the
        end-to-end latency from NOTIFY to subscriber yield is sub-
        second, large enough that idle CPU stays low.

        IMPORTANT: This async generator opens a DEDICATED long-lived
        connection (LISTEN scope is per-session). The connection is
        released on cancellation / generator close. Callers should
        scope ``listen`` to an `async with` or an `async-for` whose
        lifetime is meaningful — orphaned listeners leak connections.
        """
        _validate_channel(channel)
        async with self._engine.connect() as conn:
            # LISTEN runs once per connection. Subsequent reads come
            # from the driver's internal notify queue.
            await conn.execute(text(f"LISTEN {channel}"))
            await conn.commit()  # LISTEN is committed via implicit BEGIN
            raw_conn = await conn.get_raw_connection()
            asyncpg_conn = getattr(raw_conn, "_connection", None) or getattr(
                raw_conn, "driver_connection", None,
            )
            if asyncpg_conn is None:  # pragma: no cover
                raise RuntimeError(
                    "DistributedQueue.listen requires an asyncpg-backed engine — "
                    "no underlying asyncpg connection found."
                )
            # asyncpg exposes notifications via a callback or a queue.
            # We use the queue model: register a callback that enqueues
            # to an asyncio.Queue we own, yield from the queue.
            inbox: asyncio.Queue[NotifyPayload] = asyncio.Queue()

            def _on_notify(
                connection: Any, pid: int, channel: str, raw_payload: str,
            ) -> None:
                try:
                    inbox.put_nowait(NotifyPayload.from_canonical_json(raw_payload))
                except ValueError as exc:
                    logger.warning(
                        "discarding malformed NOTIFY on %s: %s (raw=%r)",
                        channel, exc, raw_payload,
                    )

            await asyncpg_conn.add_listener(channel, _on_notify)
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(
                            inbox.get(), timeout=poll_interval_s,
                        )
                    except asyncio.TimeoutError:
                        # No event in this poll window — keep looping.
                        # The caller's cancellation propagates here naturally.
                        continue
                    yield event
            finally:
                try:
                    await asyncpg_conn.remove_listener(channel, _on_notify)
                except Exception as exc:  # pragma: no cover
                    logger.debug("remove_listener failed (ok on shutdown): %s", exc)


# ── Helpers ──────────────────────────────────────────────────────────


def _validate_channel(channel: str) -> None:
    """Postgres identifier validation. Channel names go into LISTEN /
    NOTIFY SQL via string formatting (parameter binding doesn't work
    for identifiers); we MUST validate to prevent SQL injection.

    Postgres identifier rules: starts with a letter or underscore,
    followed by letters, digits, underscores. Max length 63 bytes.
    """
    if not isinstance(channel, str):
        raise TypeError(f"channel must be str, got {type(channel).__name__}")
    if not channel:
        raise ValueError("channel must be non-empty")
    if len(channel) > 63:
        raise ValueError(f"channel longer than 63 bytes: {len(channel)}")
    if not (channel[0].isalpha() or channel[0] == "_"):
        raise ValueError(
            f"channel must start with letter or underscore, got {channel[0]!r}"
        )
    for ch in channel[1:]:
        if not (ch.isalnum() or ch == "_"):
            raise ValueError(f"channel contains invalid character: {ch!r}")


__all__ = [
    "compute_lock_id",
    "NotifyPayload",
    "ExponentialBackoff",
    "AdvisoryLockHolder",
    "DistributedQueue",
]
