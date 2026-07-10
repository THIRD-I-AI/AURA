"""Live-server integration tests for the UASR distributed substrate.

The unit suites (``test_uasr_distributed_repair.py``,
``test_uasr_state_store.py``) exercise the coordinator and state store
against ``fakeredis``. This module re-runs the load-bearing distributed
properties against a **real** ``redis-server`` process when one is
reachable, closing the "validated against fakeredis, not a live server"
caveat for the commands we actually use (``SET NX PX`` admission, sorted
sets, TTL, cross-client shared state).

Discovery: the test looks for a live server at ``UASR_TEST_REDIS_URL``
(default ``redis://localhost:6399/0``) and skips cleanly if none answers
``PING`` -- so CI without a Redis service is unaffected, while a run with
a server present gets real-server coverage. Each test flushes the db for
isolation.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

redis = pytest.importorskip("redis")

from uasr.distributed_repair import DistributedRepairCoordinator  # noqa: E402
from uasr.models import ColumnDistribution  # noqa: E402
from uasr.models import DriftSeverity as S  # noqa: E402
from uasr.state_store import RedisStateStore, SourceState  # noqa: E402

_URL = os.getenv("UASR_TEST_REDIS_URL", "redis://localhost:6399/0")


def _live_client():
    """Return a live redis client, or skip if no server answers PING."""
    try:
        c = redis.Redis.from_url(_URL, socket_connect_timeout=1)
        c.ping()
    except Exception:
        pytest.skip(f"no live redis-server at {_URL}")
    return c


@pytest.fixture()
def live_redis():
    c = _live_client()
    c.flushdb()
    yield c
    try:
        c.flushdb()
    finally:
        c.close()


def _coord(r, node, cap=4, **kw):
    return DistributedRepairCoordinator(
        client=r, max_global_concurrent=cap, node_id=node, poll_ms=5, **kw
    )


def test_live_global_bounded_concurrency(live_redis):
    """Global cap holds across 3 nodes sharing ONE real redis server."""
    async def run():
        nodes = [_coord(live_redis, f"n{i}", cap=4) for i in range(3)]
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
    assert peak <= 4, f"global cap violated on live server: peak={peak}"


def test_live_cross_node_priority(live_redis):
    """A CRITICAL submitted later on node B preempts a LOW queued on node A."""
    async def run():
        nA = _coord(live_redis, "A", cap=1)
        nB = _coord(live_redis, "B", cap=1)
        order = []

        async def rep(tag):
            order.append(tag)
            await asyncio.sleep(0.01)
            return tag

        async def occupy():
            await nA.submit("occupy", S.LOW, lambda: rep("OCCUPY"))

        occ = asyncio.create_task(occupy())
        await asyncio.sleep(0.005)
        low = asyncio.create_task(nA.submit("low1", S.LOW, lambda: rep("LOW")))
        await asyncio.sleep(0.002)
        crit = asyncio.create_task(nB.submit("crit1", S.CRITICAL, lambda: rep("CRIT")))
        await asyncio.gather(occ, low, crit)
        return order

    order = asyncio.run(run())
    assert order.index("CRIT") < order.index("LOW"), order


def test_live_crashed_node_lease_reclamation(live_redis):
    """A live node reclaims the slot of a crashed node after lease expiry --
    the SET NX PX lease actually expires on a real server."""
    async def run():
        nDead = _coord(live_redis, "DEAD", cap=1, lease_ms=80, heartbeat_ms=40)
        nLive = _coord(live_redis, "LIVE", cap=1, lease_ms=80, heartbeat_ms=40)
        tok = nDead._enqueue("crashed_src", S.HIGH)
        assert nDead._try_admit(tok), "setup: dead node should grab the slot"

        ran = []

        async def rep():
            ran.append("LIVE_RAN")
            await asyncio.sleep(0.005)
            return "ok"

        t0 = time.perf_counter()
        await nLive.submit("live_src", S.HIGH, rep)
        waited_ms = (time.perf_counter() - t0) * 1000.0
        return ran, waited_ms, nLive.stats.reclaimed_leases

    ran, waited, reclaimed = asyncio.run(run())
    assert ran == ["LIVE_RAN"]
    assert waited >= 70.0, f"LIVE ran before dead lease expired: {waited:.0f}ms"
    assert reclaimed >= 1


def test_live_cross_replica_state_sharing(live_redis):
    """Baseline written by 'replica A' is readable by a separate
    RedisStateStore 'replica B' over the same live server (the cold-miss
    fix): shared state genuinely crosses process-equivalent clients."""
    store_a = RedisStateStore(client=live_redis)
    st = SourceState()
    st.baseline = {"amount": ColumnDistribution(column_name="amount", mean=50.0, std=5.0)}
    store_a.save("orders", st)

    store_b = RedisStateStore(client=live_redis)
    loaded = store_b.load("orders")
    assert "amount" in loaded.baseline
    assert abs(loaded.baseline["amount"].mean - 50.0) < 1e-9
    assert "orders" in store_b.source_ids()
