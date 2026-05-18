"""
Sprint 20b — Layer 17a contract tests for distributed scheduler primitives.

Two tiers:

  Tier A — pure-Python (no Postgres required, runs on base backend lane):
      * compute_lock_id determinism + bigint range + collision-resistance
      * NotifyPayload canonical-JSON roundtrip + malformed-input rejection
      * ExponentialBackoff state machine + parameter validation
      * AdvisoryLockHolder constructor validation
      * Channel-name validation (SQL-injection prevention)

  Tier B — Postgres-required (gated by AURA_PG_TEST_DSN env var; runs in
  the new scheduler-distributed-test CI lane with a postgres:16 service):
      * LISTEN/NOTIFY round-trip
      * NOTIFY payload rejection at 8KB cap
      * AdvisoryLockHolder contention across two connections
      * AdvisoryLockHolder release on context exit (next acquire succeeds)
      * AdvisoryLockHolder timeout returns False without raising

The tiered structure follows [[feedback_optional_dep_test_gating]] —
Tier B file is in the same module but ALL Postgres-dependent tests
sit behind a single ``postgres_required`` marker that skips with a
clear message when the env var is absent. The CI lane installs the
deps, sets the env var, and runs the file end-to-end; a silent skip
on the base lane is acceptable BECAUSE the new lane runs the same
file with the env var set.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from scheduler_service.distributed_queue import (
    AdvisoryLockHolder,
    DistributedQueue,
    ExponentialBackoff,
    NotifyPayload,
    compute_lock_id,
)

# ── Tier A — pure-Python primitives (no database dependency) ─────────


# ── compute_lock_id ────────────────────────────────────────────────────


def test_compute_lock_id_is_deterministic() -> None:
    """Two replicas calling compute_lock_id with the same name MUST
    get the same bigint — that's how they contend on the same Postgres
    advisory lock. If this drifts, leader election breaks."""
    assert compute_lock_id("cron_evaluator") == compute_lock_id("cron_evaluator")
    assert compute_lock_id("") == compute_lock_id("")


def test_compute_lock_id_stays_inside_bigint_range() -> None:
    """Postgres advisory-lock keys are signed 64-bit bigint. A derivation
    that drifts outside that range would silently fail at runtime —
    pg_advisory_lock rejects out-of-range values with a confusing error."""
    keys = [compute_lock_id(f"name_{i}") for i in range(200)]
    for k in keys:
        assert -(1 << 63) <= k <= (1 << 63) - 1, f"key {k} outside bigint range"


def test_compute_lock_id_collision_resistance_over_1000_names() -> None:
    """1000 distinct names should produce 1000 distinct keys. The
    birthday-paradox probability of a collision in 1000 64-bit draws
    is ≈ 2.7×10⁻¹⁴ so a collision here means the derivation is broken."""
    keys = {compute_lock_id(f"job_{i}") for i in range(1000)}
    assert len(keys) == 1000


def test_compute_lock_id_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="name must be str"):
        compute_lock_id(42)  # type: ignore[arg-type]


# ── NotifyPayload ────────────────────────────────────────────────────


def test_notify_payload_canonical_roundtrip() -> None:
    """Encode → decode must produce an equal payload. Required for
    Layer 10 audit replay in S20.2."""
    p = NotifyPayload(
        kind="job_inserted", job_id="abc123",
        payload={"priority": 5, "tags": ["urgent", "billing"]},
    )
    encoded = p.to_canonical_json()
    decoded = NotifyPayload.from_canonical_json(encoded)
    assert decoded == p


def test_notify_payload_canonical_is_deterministic_across_dict_orderings() -> None:
    """sort_keys=True must make the encoding independent of dict
    insertion order. Two replicas constructing the same logical
    payload from differently-ordered fields must produce byte-identical
    encodings — otherwise audit hashes drift."""
    p1 = NotifyPayload(kind="x", job_id="j", payload={"a": 1, "b": 2})
    p2 = NotifyPayload(kind="x", job_id="j", payload={"b": 2, "a": 1})
    assert p1.to_canonical_json() == p2.to_canonical_json()


def test_notify_payload_compact_separators() -> None:
    """No whitespace in the canonical encoding — every byte counts when
    the payload approaches the 8KB Postgres cap."""
    p = NotifyPayload(kind="x", job_id="", payload={})
    encoded = p.to_canonical_json()
    assert " " not in encoded and "\n" not in encoded


def test_notify_payload_rejects_missing_fields() -> None:
    """Decoder must reject malformed JSON rather than silently filling
    defaults — a subscriber processing a malformed event would be a
    hard-to-diagnose bug."""
    with pytest.raises(ValueError, match="missing required field"):
        NotifyPayload.from_canonical_json('{"kind": "x", "job_id": "j"}')


def test_notify_payload_rejects_non_dict_top_level() -> None:
    with pytest.raises(ValueError, match="must decode to dict"):
        NotifyPayload.from_canonical_json('["not", "a", "dict"]')


def test_notify_payload_rejects_wrong_field_types() -> None:
    with pytest.raises(ValueError, match="kind must be str"):
        NotifyPayload.from_canonical_json('{"kind": 42, "job_id": "j", "payload": {}}')


# ── ExponentialBackoff ───────────────────────────────────────────────


def test_backoff_deterministic_when_jitter_zero() -> None:
    """Pure-function semantics when jitter=0: same step count → same
    delay. Used for Layer 10 byte-identity in S20.2."""
    b = ExponentialBackoff(base_delay_s=0.1, factor=2.0, max_delay_s=10.0, jitter=0.0)
    delays = [b.next_delay() for _ in range(5)]
    assert delays == [0.1, 0.2, 0.4, 0.8, 1.6]


def test_backoff_caps_at_max_delay() -> None:
    """At high step counts, delay must clamp to max_delay_s — without
    the cap, exponential growth would produce minutes-long sleeps."""
    b = ExponentialBackoff(base_delay_s=1.0, factor=2.0, max_delay_s=5.0, jitter=0.0)
    for _ in range(20):
        b.next_delay()
    # After many steps, all subsequent delays must equal max_delay_s.
    assert b.next_delay() == 5.0


def test_backoff_reset_returns_to_step_zero() -> None:
    """Caller resets after a successful acquisition so the NEXT
    contention starts from the short base delay, not the inflated
    post-contention delay."""
    b = ExponentialBackoff(base_delay_s=0.1, factor=2.0, max_delay_s=10.0, jitter=0.0)
    for _ in range(5):
        b.next_delay()
    assert b.step == 5
    b.reset()
    assert b.step == 0
    assert b.next_delay() == 0.1


def test_backoff_jitter_introduces_randomness() -> None:
    """With jitter > 0, two backoff instances created with the same
    params produce DIFFERENT delays (with overwhelming probability).
    Spreads contention across replicas — prevents thundering herd."""
    b1 = ExponentialBackoff(base_delay_s=0.1, factor=2.0, jitter=0.5)
    b2 = ExponentialBackoff(base_delay_s=0.1, factor=2.0, jitter=0.5)
    # 5 successive draws are extraordinarily unlikely to match exactly.
    draws_1 = [b1.next_delay() for _ in range(5)]
    draws_2 = [b2.next_delay() for _ in range(5)]
    assert draws_1 != draws_2


@pytest.mark.parametrize(
    "kwargs,error_pattern",
    [
        ({"base_delay_s": 0}, "base_delay_s must be > 0"),
        ({"factor": 0.5}, "factor must be >= 1.0"),
        ({"base_delay_s": 10.0, "max_delay_s": 1.0}, "max_delay_s.*must be >="),
        ({"jitter": 1.5}, "jitter must be in"),
        ({"jitter": -0.1}, "jitter must be in"),
    ],
)
def test_backoff_constructor_validates(kwargs: dict, error_pattern: str) -> None:
    """Constructor must reject invalid parameter combinations rather
    than producing a quietly-broken controller."""
    with pytest.raises(ValueError, match=error_pattern):
        ExponentialBackoff(**kwargs)


# ── AdvisoryLockHolder constructor validation ─────────────────────────


def test_advisory_lock_holder_rejects_non_int_lock_id() -> None:
    """Postgres bigint is int; a string or float would silently lose
    precision or fail at runtime with a Postgres syntax error."""
    with pytest.raises(TypeError, match="lock_id must be int"):
        AdvisoryLockHolder(engine=None, lock_id="abc")  # type: ignore[arg-type]


def test_advisory_lock_holder_rejects_out_of_range_lock_id() -> None:
    with pytest.raises(ValueError, match="outside Postgres bigint range"):
        AdvisoryLockHolder(engine=None, lock_id=1 << 65)


def test_advisory_lock_holder_rejects_negative_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_s must be >= 0"):
        AdvisoryLockHolder(engine=None, lock_id=1, timeout_s=-1.0)


# ── Channel name validation (SQL-injection prevention) ───────────────


def test_distributed_queue_rejects_invalid_channel_chars() -> None:
    """Channel names go into LISTEN/NOTIFY SQL via string formatting
    (Postgres doesn't support parameter binding for identifiers).
    A semicolon or quote would enable SQL injection — validation MUST
    reject them at the boundary."""
    from scheduler_service.distributed_queue import _validate_channel
    # Valid names pass.
    _validate_channel("scheduler_jobs")
    _validate_channel("_private")
    _validate_channel("channel_123")
    # Invalid names raise.
    with pytest.raises(ValueError, match="invalid character"):
        _validate_channel("bad;DROP TABLE")
    with pytest.raises(ValueError, match="invalid character"):
        _validate_channel("with spaces")
    with pytest.raises(ValueError, match="start with letter"):
        _validate_channel("1starts_with_digit")
    with pytest.raises(ValueError, match="non-empty"):
        _validate_channel("")
    with pytest.raises(ValueError, match="longer than 63"):
        _validate_channel("x" * 64)


# ── Tier B — Postgres-required integration tests ──────────────────────


PG_DSN = os.getenv("AURA_PG_TEST_DSN")
postgres_required = pytest.mark.skipif(
    not PG_DSN,
    reason=(
        "AURA_PG_TEST_DSN not set; the scheduler-distributed-test CI lane "
        "provisions postgres:16 as a service container and sets this env var. "
        "Set locally to a postgresql+asyncpg://... DSN to run integration tests."
    ),
)


@pytest.fixture
async def pg_engine():
    """Async SQLAlchemy engine pointed at a real Postgres. Yields the
    engine; disposes on exit so the test process can shut down cleanly."""
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(PG_DSN, echo=False, future=True, pool_size=5)
    yield engine
    await engine.dispose()


@postgres_required
@pytest.mark.asyncio
async def test_advisory_lock_acquire_then_release(pg_engine) -> None:
    """Single-holder lifecycle: acquire, do work, release. After
    release, a new holder for the same key MUST be able to acquire."""
    key = compute_lock_id("test_acquire_release")
    async with AdvisoryLockHolder(pg_engine, lock_id=key, timeout_s=5.0) as got:
        assert got is True
    # Now a second holder with the same key acquires immediately.
    async with AdvisoryLockHolder(pg_engine, lock_id=key, timeout_s=1.0) as got2:
        assert got2 is True


@postgres_required
@pytest.mark.asyncio
async def test_advisory_lock_contention_two_holders(pg_engine) -> None:
    """The core leader-election contract: two replicas contending on
    the same key — exactly ONE wins. The second's __aenter__ must
    return False after timeout (not raise, not block forever)."""
    key = compute_lock_id("test_contention")
    async with AdvisoryLockHolder(pg_engine, lock_id=key, timeout_s=10.0) as a:
        assert a is True
        # Second holder contends; should time out without acquiring.
        async with AdvisoryLockHolder(pg_engine, lock_id=key, timeout_s=0.5) as b:
            assert b is False


@postgres_required
@pytest.mark.asyncio
async def test_advisory_lock_release_on_exception(pg_engine) -> None:
    """Lock must release even if the with-body raises. Otherwise a
    bug in the critical section would deadlock the cluster permanently."""
    key = compute_lock_id("test_exception_release")
    with pytest.raises(RuntimeError, match="boom"):
        async with AdvisoryLockHolder(pg_engine, lock_id=key, timeout_s=5.0):
            raise RuntimeError("boom")
    # If lock didn't release, this would time out.
    async with AdvisoryLockHolder(pg_engine, lock_id=key, timeout_s=1.0) as got:
        assert got is True


@postgres_required
@pytest.mark.asyncio
async def test_listen_notify_round_trip(pg_engine) -> None:
    """A NOTIFY published on a channel must be received by a LISTENing
    subscriber within seconds. This is the latency win over polling —
    pre-S20b the scheduler polled every 60s."""
    queue = DistributedQueue(pg_engine)
    received: list[NotifyPayload] = []

    async def _subscribe() -> None:
        async for evt in queue.listen("aura_test_channel"):
            received.append(evt)
            break  # one event is enough

    sub_task = asyncio.create_task(_subscribe())
    # Give the subscriber a moment to register LISTEN before we publish.
    await asyncio.sleep(0.2)
    await queue.notify(
        "aura_test_channel",
        NotifyPayload(kind="ping", job_id="j1", payload={"v": 1}),
    )
    # Wait for the subscriber to consume + exit.
    await asyncio.wait_for(sub_task, timeout=5.0)
    assert len(received) == 1
    assert received[0].kind == "ping"
    assert received[0].job_id == "j1"


@postgres_required
@pytest.mark.asyncio
async def test_notify_payload_rejects_oversize(pg_engine) -> None:
    """8KB cap defended at the wrapper — without this, a too-big payload
    silently fails at the Postgres protocol layer with a confusing error."""
    queue = DistributedQueue(pg_engine)
    big = NotifyPayload(kind="x", job_id="", payload={"data": "x" * 8000})
    with pytest.raises(ValueError, match="must be < 7900"):
        await queue.notify("aura_test_channel", big)
