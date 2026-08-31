"""R4 — RepairScheduler regression tests.

Proves the global multi-pipeline repair scheduler's four guaranteed properties:
bounded concurrency, priority ordering, FIFO fairness within a severity, and
result/exception propagation back to the submitter.
"""
from __future__ import annotations

import asyncio
import functools

import pytest

from uasr.models import DriftSeverity
from uasr.repair_scheduler import RepairScheduler


@pytest.mark.asyncio
async def test_bounded_concurrency():
    """Never more than max_concurrent repairs run at once, regardless of N."""
    sched = RepairScheduler(max_concurrent=4)
    await sched.start()

    async def work():
        await asyncio.sleep(0.02)
        return "ok"

    tasks = [
        asyncio.create_task(sched.submit(f"s{i}", DriftSeverity.MEDIUM, work))
        for i in range(40)
    ]
    results = await asyncio.gather(*tasks)
    await sched.stop()

    assert all(r == "ok" for r in results)
    assert sched.stats.max_observed_concurrency <= 4
    assert sched.stats.max_observed_concurrency == 4  # actually saturates
    assert sched.stats.completed == 40


@pytest.mark.asyncio
async def test_priority_ordering():
    """A CRITICAL repair jumps ahead of an earlier LOW/MEDIUM backlog."""
    sched = RepairScheduler(max_concurrent=1)  # serialize to observe order
    await sched.start()
    admitted = []

    async def track(name):
        admitted.append(name)
        await asyncio.sleep(0.005)
        return name

    # Occupy the single slot so a backlog builds behind it.
    occ = asyncio.create_task(
        sched.submit("occ", DriftSeverity.LOW, functools.partial(track, "occupy"))
    )
    await asyncio.sleep(0.002)
    subs = [
        asyncio.create_task(sched.submit("low1", DriftSeverity.LOW, functools.partial(track, "low1"))),
        asyncio.create_task(sched.submit("med1", DriftSeverity.MEDIUM, functools.partial(track, "med1"))),
        asyncio.create_task(sched.submit("crit1", DriftSeverity.CRITICAL, functools.partial(track, "crit1"))),
        asyncio.create_task(sched.submit("high1", DriftSeverity.HIGH, functools.partial(track, "high1"))),
        asyncio.create_task(sched.submit("crit2", DriftSeverity.CRITICAL, functools.partial(track, "crit2"))),
    ]
    await asyncio.gather(occ, *subs)
    await sched.stop()

    # occupy ran first (already admitted); the backlog drains in priority order.
    assert admitted[0] == "occupy"
    assert admitted[1:] == ["crit1", "crit2", "high1", "med1", "low1"]


@pytest.mark.asyncio
async def test_fifo_within_severity():
    """Equal-severity repairs are admitted in submission order (no starvation)."""
    sched = RepairScheduler(max_concurrent=1)
    await sched.start()
    order = []

    async def rec(name):
        order.append(name)
        await asyncio.sleep(0.003)
        return name

    occ = asyncio.create_task(
        sched.submit("occ", DriftSeverity.LOW, functools.partial(rec, "occupy"))
    )
    await asyncio.sleep(0.002)
    fifo = [
        asyncio.create_task(sched.submit(f"h{i}", DriftSeverity.HIGH, functools.partial(rec, f"h{i}")))
        for i in range(5)
    ]
    await asyncio.gather(occ, *fifo)
    await sched.stop()

    assert order[1:] == ["h0", "h1", "h2", "h3", "h4"]


@pytest.mark.asyncio
async def test_result_and_exception_propagation():
    """submit() resolves to the repair's result, or re-raises its exception."""
    sched = RepairScheduler(max_concurrent=2)
    await sched.start()

    async def good():
        return 42

    async def bad():
        raise ValueError("repair failed")

    r = await sched.submit("s_good", DriftSeverity.HIGH, good)
    assert r == 42

    with pytest.raises(ValueError, match="repair failed"):
        await sched.submit("s_bad", DriftSeverity.HIGH, bad)

    await sched.stop()
    assert sched.stats.completed == 1
    assert sched.stats.failed == 1


@pytest.mark.asyncio
async def test_wait_stats_observable():
    """Per-severity admission wait times are recorded for observability."""
    sched = RepairScheduler(max_concurrent=1)
    await sched.start()

    async def work():
        await asyncio.sleep(0.01)
        return "ok"

    tasks = [
        asyncio.create_task(sched.submit(f"s{i}", DriftSeverity.MEDIUM, work))
        for i in range(4)
    ]
    await asyncio.gather(*tasks)
    await sched.stop()

    summary = sched.stats.wait_summary()
    assert "medium" in summary
    # With serialized execution, later arrivals wait progressively longer.
    assert sched.stats.per_severity_admitted["medium"] == 4
    assert summary["medium"] >= 0.0


@pytest.mark.asyncio
async def test_rejects_bad_capacity():
    with pytest.raises(ValueError):
        RepairScheduler(max_concurrent=0)


@pytest.mark.asyncio
async def test_rejects_negative_per_source_cap():
    with pytest.raises(ValueError):
        RepairScheduler(max_concurrent=4, max_per_source=-1)


# ── per-source fairness (opt-in via max_per_source) ────────────────────

@pytest.mark.asyncio
async def test_per_source_cap_off_by_default_matches_bounded_concurrency():
    """max_per_source=0 (the default) must not change existing behaviour --
    one source can still fill every global slot."""
    sched = RepairScheduler(max_concurrent=4)
    await sched.start()

    async def work():
        await asyncio.sleep(0.02)
        return "ok"

    tasks = [
        asyncio.create_task(sched.submit("noisy", DriftSeverity.MEDIUM, work))
        for _ in range(40)
    ]
    results = await asyncio.gather(*tasks)
    await sched.stop()

    assert all(r == "ok" for r in results)
    assert sched.stats.max_observed_concurrency == 4


@pytest.mark.asyncio
async def test_per_source_cap_prevents_starvation():
    """A single noisy source's backlog must not occupy every global slot --
    a different source's repair is admitted well before the noisy backlog
    drains, even though it arrives after most of the backlog."""
    sched = RepairScheduler(max_concurrent=4, max_per_source=2)
    await sched.start()
    admitted_order: list[str] = []

    async def work(name: str):
        admitted_order.append(name)
        await asyncio.sleep(0.02)
        return name

    noisy = [
        asyncio.create_task(
            sched.submit("noisy_source", DriftSeverity.MEDIUM, functools.partial(work, f"noisy{i}"))
        )
        for i in range(10)
    ]
    await asyncio.sleep(0.002)  # let the noisy backlog queue up first
    other = asyncio.create_task(
        sched.submit("quiet_source", DriftSeverity.MEDIUM, functools.partial(work, "quiet"))
    )
    await asyncio.gather(*noisy, other)
    await sched.stop()

    # At most 2 of "noisy_source"'s repairs ran concurrently at any point --
    # proven indirectly: "quiet" was admitted well before the 10-item noisy
    # backlog finished draining (it would be admitted 9th/10th under a
    # source-blind FIFO/priority queue since it arrived after the backlog).
    assert admitted_order.index("quiet") < 8


@pytest.mark.asyncio
async def test_per_source_cap_never_drops_a_skipped_item():
    """An item skipped for being over its source's cap is requeued, not
    discarded -- it must still eventually complete."""
    sched = RepairScheduler(max_concurrent=1, max_per_source=1)
    await sched.start()

    async def work(name: str):
        await asyncio.sleep(0.005)
        return name

    tasks = [
        asyncio.create_task(sched.submit("s1", DriftSeverity.MEDIUM, functools.partial(work, f"s1_{i}")))
        for i in range(3)
    ] + [
        asyncio.create_task(sched.submit("s2", DriftSeverity.MEDIUM, functools.partial(work, f"s2_{i}")))
        for i in range(3)
    ]
    results = await asyncio.gather(*tasks)
    await sched.stop()

    assert sorted(results) == sorted([f"s1_{i}" for i in range(3)] + [f"s2_{i}" for i in range(3)])
    assert sched.stats.completed == 6


@pytest.mark.asyncio
async def test_active_count_for_source_tracks_and_clears():
    sched = RepairScheduler(max_concurrent=4, max_per_source=2)
    await sched.start()
    release = asyncio.Event()

    async def work():
        await release.wait()
        return "ok"

    tasks = [
        asyncio.create_task(sched.submit("s1", DriftSeverity.MEDIUM, work))
        for _ in range(2)
    ]
    await asyncio.sleep(0.005)
    assert sched.active_count_for_source("s1") == 2

    release.set()
    await asyncio.gather(*tasks)
    await sched.stop()
    assert sched.active_count_for_source("s1") == 0
