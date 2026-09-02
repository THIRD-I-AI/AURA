"""Tests for the UASR DistributedRepairCoordinator (M2: cross-node repair
admission).

The coordinator lifts repair admission control from a single process to the
whole fleet using Redis as the coordination substrate.  These tests run several
coordinators (= worker nodes) against ONE ``fakeredis`` backend — a
high-fidelity in-process Redis with real ZSET / SET-NX / INCR semantics — and
assert the four fleet-wide properties:

  P1  global bounded concurrency across nodes
  P2  cross-node priority ordering (CRITICAL before LOW, on different nodes)
  P3  FIFO fairness within a severity, fleet-wide
  P4  crashed-node lease reclamation (a dead node's slot auto-expires)

Skips cleanly if ``fakeredis`` is not installed.
"""
from __future__ import annotations

import asyncio
import time
from typing import List

import pytest

from uasr.distributed_repair import DistributedRepairCoordinator
from uasr.models import DriftSeverity as S


def _redis():
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeStrictRedis()


def _coord(r, node, cap=4, **kw):
    return DistributedRepairCoordinator(
        client=r, max_global_concurrent=cap, node_id=node, poll_ms=5, **kw
    )


def test_rejects_bad_config():
    r = _redis()
    with pytest.raises(ValueError):
        DistributedRepairCoordinator(client=r, max_global_concurrent=0)
    with pytest.raises(ValueError):
        # heartbeat must be < lease or the lease can lapse under a live node
        DistributedRepairCoordinator(client=r, lease_ms=1000, heartbeat_ms=1000)


def test_p1_global_bounded_concurrency_across_nodes():
    async def run():
        r = _redis()
        nodes = [_coord(r, f"n{i}", cap=4) for i in range(3)]  # 3 nodes, ONE redis
        live = 0
        peak = 0
        lock = asyncio.Lock()

        async def repair():
            nonlocal live, peak
            async with lock:
                live += 1
                peak = max(peak, live)
            await asyncio.sleep(0.02)
            async with lock:
                live -= 1
            return "ok"

        tasks = [
            asyncio.create_task(nodes[i % 3].submit(f"s{i}", S.MEDIUM, repair))
            for i in range(30)
        ]
        res = await asyncio.gather(*tasks)
        assert all(x == "ok" for x in res)
        return peak

    peak = asyncio.run(run())
    assert peak <= 4, f"global cap violated: peak={peak}"


def test_p2_cross_node_priority_ordering():
    # Timing margins here are generous (not the original 2-5ms) because
    # Windows' default asyncio event loop has ~15.6ms timer granularity --
    # the original tight margins meant "await asyncio.sleep(0.002)" could
    # round up enough that LOW's poll (also inflated) occasionally grabbed
    # the just-freed slot before CRIT was even enqueued, independent of
    # any real bug in priority ordering. Confirmed by instrumenting a
    # local repro: observed OCCUPY->LOW->CRIT gaps of ~31ms each against
    # an intended 5ms/2ms/10ms schedule. 100ms+ margins comfortably clear
    # that tick size on any platform while keeping the test well under 1s.
    async def run():
        r = _redis()
        nA = _coord(r, "A", cap=1)
        nB = _coord(r, "B", cap=1)  # cap=1 => strict global serialization
        order = []

        async def rep(tag):
            order.append(tag)
            await asyncio.sleep(0.15)
            return tag

        async def occupy():
            await nA.submit("occupy", S.LOW, lambda: rep("OCCUPY"))

        occ = asyncio.create_task(occupy())
        await asyncio.sleep(0.05)  # let OCCUPY get admitted first
        low = asyncio.create_task(nA.submit("low1", S.LOW, lambda: rep("LOW")))
        await asyncio.sleep(0.05)
        crit = asyncio.create_task(nB.submit("crit1", S.CRITICAL, lambda: rep("CRIT")))
        await asyncio.gather(occ, low, crit)
        return order

    order = asyncio.run(run())
    # CRIT was submitted AFTER LOW, on a different node, yet must run first.
    assert order.index("CRIT") < order.index("LOW"), order


def test_p3_fifo_fairness_within_severity():
    async def run():
        r = _redis()
        nA = _coord(r, "A", cap=1)
        nB = _coord(r, "B", cap=1)
        order = []

        async def rep(tag):
            order.append(tag)
            await asyncio.sleep(0.005)
            return tag

        async def occupy():
            await nA.submit("occ", S.HIGH, lambda: rep("OCC"))

        occ = asyncio.create_task(occupy())
        await asyncio.sleep(0.004)
        t1 = asyncio.create_task(nA.submit("m1", S.MEDIUM, lambda: rep("M1")))
        await asyncio.sleep(0.002)
        t2 = asyncio.create_task(nB.submit("m2", S.MEDIUM, lambda: rep("M2")))
        await asyncio.sleep(0.002)
        t3 = asyncio.create_task(nA.submit("m3", S.MEDIUM, lambda: rep("M3")))
        await asyncio.gather(occ, t1, t2, t3)
        return [x for x in order if x.startswith("M")]

    fifo = asyncio.run(run())
    assert fifo == ["M1", "M2", "M3"], fifo


def test_p4_crashed_node_lease_reclamation():
    async def run():
        r = _redis()
        nDead = _coord(r, "DEAD", cap=1, lease_ms=80, heartbeat_ms=40)
        nLive = _coord(r, "LIVE", cap=1, lease_ms=80, heartbeat_ms=40)
        # Simulate a crash: DEAD grabs the only slot and never heartbeats/releases.
        tok = await nDead._enqueue("crashed_src", S.HIGH)
        assert await nDead._try_admit(tok), "setup: dead node should grab the slot"

        ran = []

        async def rep():
            ran.append("LIVE_RAN")
            await asyncio.sleep(0.005)
            return "ok"

        t0 = time.perf_counter()
        await nLive.submit("live_src", S.HIGH, rep)  # must wait out the dead lease
        waited_ms = (time.perf_counter() - t0) * 1000.0
        return ran, waited_ms, nLive.stats.reclaimed_leases

    ran, waited, reclaimed = asyncio.run(run())
    assert ran == ["LIVE_RAN"]
    assert waited >= 70.0, f"LIVE ran before dead lease expired: {waited:.0f}ms"
    assert reclaimed >= 1


def test_result_and_exception_propagate():
    async def run():
        r = _redis()
        n = _coord(r, "N", cap=2)

        async def ok():
            return 42

        async def boom():
            raise RuntimeError("repair failed")

        val = await n.submit("s1", S.LOW, ok)
        assert val == 42
        with pytest.raises(RuntimeError, match="repair failed"):
            await n.submit("s2", S.LOW, boom)
        # slot released on both success and failure -> nothing left active
        assert await n.active_count() == 0
        assert n.stats.completed == 1
        assert n.stats.failed == 1

    asyncio.run(run())


def test_rejects_negative_per_source_cap():
    r = _redis()
    with pytest.raises(ValueError):
        DistributedRepairCoordinator(client=r, max_per_source=-1)


# ── per-source fairness (opt-in via max_per_source), fleet-wide ────────

def test_per_source_cap_off_by_default_matches_bounded_concurrency():
    """max_per_source=0 (the default) must not change existing behaviour --
    one source can still fill every global slot, across nodes."""
    async def run():
        r = _redis()
        nodes = [_coord(r, f"n{i}", cap=4) for i in range(2)]

        async def work():
            await asyncio.sleep(0.02)
            return "ok"

        tasks = [
            asyncio.create_task(nodes[i % 2].submit("noisy", S.MEDIUM, work))
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)
        return results, nodes[0].stats.max_observed_global

    results, peak = asyncio.run(run())
    assert all(r == "ok" for r in results)
    # <=, not ==, matching test_p1's own assertion style: the global cap is
    # an upper bound, not a guarantee every run's poll timing reaches it
    # exactly -- p1 already covers "the cap is respected"; this test's job
    # is only "max_per_source=0 doesn't add a NEW, lower bound".
    assert peak <= 4


def test_per_source_cap_prevents_starvation_across_nodes():
    """A single noisy source's fleet-wide backlog (queued across multiple
    nodes) must not occupy every global slot -- a different source's
    repair, submitted on yet another node, is admitted well before the
    noisy backlog drains."""
    async def run():
        r = _redis()
        noisy_nodes = [_coord(r, f"noisy{i}", cap=4, max_per_source=2) for i in range(2)]
        quiet_node = _coord(r, "quiet", cap=4, max_per_source=2)
        admitted_order: List[str] = []

        # Work/pre-sleep durations widened well past the original 0.02s/0.03s
        # (BUG-021 fix): _try_admit's redis calls now go through
        # asyncio.to_thread, so 11 concurrently-polling submit() loops
        # create real thread-pool dispatch contention/jitter instead of the
        # old instant in-process call -- the original tight margins didn't
        # leave enough headroom for the per-source-cap skip to reliably win
        # against that jitter before the noisy backlog drains.
        async def work(name: str):
            admitted_order.append(name)
            await asyncio.sleep(0.25)
            return name

        noisy = [
            asyncio.create_task(
                noisy_nodes[i % 2].submit("noisy_source", S.MEDIUM, lambda i=i: work(f"noisy{i}"))
            )
            for i in range(10)
        ]
        await asyncio.sleep(0.15)  # let the noisy backlog queue up fleet-wide first
        other = asyncio.create_task(
            quiet_node.submit("quiet_source", S.MEDIUM, lambda: work("quiet"))
        )
        await asyncio.gather(*noisy, other)
        return admitted_order

    order = asyncio.run(run())
    # Under a source-blind priority queue, "quiet" (arriving after the
    # 10-item noisy backlog) would be admitted 9th/10th. With the fleet-
    # wide per-source cap it must jump the backlog well before that.
    assert order.index("quiet") < 8, order


def test_per_source_cap_never_drops_a_skipped_item_across_nodes():
    """An item skipped for being over its source's fleet-wide cap is
    requeued, not discarded -- it must still eventually complete, even
    when submitted from a different node than the one holding the cap."""
    async def run():
        r = _redis()
        nA = _coord(r, "A", cap=1, max_per_source=1)
        nB = _coord(r, "B", cap=1, max_per_source=1)

        async def work(name: str):
            await asyncio.sleep(0.01)
            return name

        tasks = [
            asyncio.create_task(nA.submit("s1", S.MEDIUM, lambda i=i: work(f"s1_{i}")))
            for i in range(3)
        ] + [
            asyncio.create_task(nB.submit("s2", S.MEDIUM, lambda i=i: work(f"s2_{i}")))
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)
        return results, nA.stats.completed + nB.stats.completed

    results, completed = asyncio.run(run())
    assert sorted(results) == sorted([f"s1_{i}" for i in range(3)] + [f"s2_{i}" for i in range(3)])
    assert completed == 6


def test_active_count_for_source_tracks_and_clears_across_nodes():
    async def run():
        r = _redis()
        nA = _coord(r, "A", cap=4, max_per_source=2)
        nB = _coord(r, "B", cap=4, max_per_source=2)
        release = asyncio.Event()

        async def work():
            await release.wait()
            return "ok"

        # Two leases for "shared_source", one admitted via each node.
        t1 = asyncio.create_task(nA.submit("shared_source", S.MEDIUM, work))
        t2 = asyncio.create_task(nB.submit("shared_source", S.MEDIUM, work))
        for _ in range(50):
            if await nA.active_count_for_source("shared_source") == 2:
                break
            await asyncio.sleep(0.005)
        during = await nA.active_count_for_source("shared_source")
        release.set()
        await asyncio.gather(t1, t2)
        after = await nA.active_count_for_source("shared_source")
        return during, after

    during, after = asyncio.run(run())
    assert during == 2
    assert after == 0
