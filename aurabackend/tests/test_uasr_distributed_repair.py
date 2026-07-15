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
    async def run():
        r = _redis()
        nA = _coord(r, "A", cap=1)
        nB = _coord(r, "B", cap=1)  # cap=1 => strict global serialization
        order = []

        async def rep(tag):
            order.append(tag)
            await asyncio.sleep(0.01)
            return tag

        async def occupy():
            await nA.submit("occupy", S.LOW, lambda: rep("OCCUPY"))

        occ = asyncio.create_task(occupy())
        await asyncio.sleep(0.005)  # let OCCUPY get admitted first
        low = asyncio.create_task(nA.submit("low1", S.LOW, lambda: rep("LOW")))
        await asyncio.sleep(0.002)
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
        tok = nDead._enqueue("crashed_src", S.HIGH)
        assert nDead._try_admit(tok), "setup: dead node should grab the slot"

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
        assert n.active_count() == 0
        assert n.stats.completed == 1
        assert n.stats.failed == 1

    asyncio.run(run())
