"""E8 — Horizontal scaling. Across a FLEET of N nodes sharing one Redis, does
aggregate ingest/detection throughput scale out with N while the shared repair
backend stays globally bounded?

Two properties an enterprise self-healing layer must have to scale horizontally:

  (a) **Detection scales out linearly.** Each node runs its own DriftDetector.
      With Redis-shared state, adding nodes adds detection capacity — aggregate
      detect throughput should grow ~linearly with N (the work is embarrassingly
      parallel; the only shared thing is state, one round-trip per batch).

  (b) **Repair stays globally bounded.** Every node routes recoveries through a
      DistributedRepairCoordinator against the SAME Redis. No matter how many
      nodes submit CRITICAL repairs at once, the fleet runs at most
      ``max_global_concurrent`` — so the shared synthesis/validation backend
      sees a flat load. An *un*coordinated fleet's load would grow linearly with
      N (each node admits its own max), swamping the backend.

Redis is provided by fakeredis (real ZSET/SET-NX semantics), shared across all
node coordinators — the same object a real deployment points at a live server.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from uasr.eval._harness import RESULTS_DIR, healthy_numeric, make_batch, summarize

REPAIR_COST_S = 0.05
GLOBAL_CAP = 8


def _make_shared_redis():
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeStrictRedis()


# ── (a) detection scale-out (genuine multiprocess) ───────────────────
def _node_detect_worker(args):
    """One node: register a baseline, detect a fixed number of batches, return
    (batches, wall_seconds). Runs in its OWN process — genuine parallel core."""
    import time as _t

    import numpy as _np

    from uasr.drift_detector import DriftDetector as _DD
    from uasr.eval._harness import healthy_numeric as _hn
    from uasr.eval._harness import make_batch as _mb
    seed, batches_per_node, n = args
    rng = _np.random.default_rng(seed)
    d = _DD(default_zeta=0.15)
    d.register_baseline("s", _mb("s", "base", {"v": _hn(rng, n=n)}))
    batches = [_mb("s", f"b{i}", {"v": _hn(rng, n=n)}) for i in range(batches_per_node)]
    t0 = _t.perf_counter()
    for b in batches:
        d.detect(b)
    return batches_per_node, _t.perf_counter() - t0


def exp_detection_scaleout(node_counts=(1, 2, 4, 8, 16), batches_per_node=1500, n=500):
    """Each node runs in its own process (shared-nothing compute; in a real fleet
    the only shared thing is Redis state, one keyed round-trip per batch). We
    measure AGGREGATE throughput = total batches / wall as node count grows, so
    linear scale-out shows up as ~N until physical cores saturate."""
    import concurrent.futures as _cf
    rows = []
    single = None
    for N in node_counts:
        total = N * batches_per_node
        tps = []
        for rep in range(3):
            args = [(42 + rep * 100 + k, batches_per_node, n) for k in range(N)]
            t0 = time.perf_counter()
            with _cf.ProcessPoolExecutor(max_workers=N) as ex:
                list(ex.map(_node_detect_worker, args))
            wall = time.perf_counter() - t0
            tps.append(total / wall)
        tp = float(np.median(tps))
        if single is None:
            single = tp
        rows.append({"experiment": "detection_scaleout", "n_nodes": N,
                     "total_batches": total,
                     "aggregate_throughput_batch_per_s": round(tp, 1),
                     "scaleout_factor": round(tp / single, 2)})
    return rows


# ── (b) repair stays globally bounded ────────────────────────────────
async def _fleet_repair(N: int, coordinated: bool, per_node_crit=4):
    """N nodes each submit `per_node_crit` CRITICAL repairs at once.
    coordinated=True: all share ONE global cap via one Redis.
    coordinated=False: each node runs its own local scheduler (cap each).
    Returns peak concurrent repairs actually running across the fleet."""
    from uasr.distributed_repair import DistributedRepairCoordinator
    from uasr.models import DriftSeverity
    from uasr.repair_scheduler import RepairScheduler

    running = 0
    peak = 0
    lock = asyncio.Lock()

    async def repair():
        nonlocal running, peak
        async with lock:
            running += 1
            peak = max(peak, running)
        await asyncio.sleep(REPAIR_COST_S)
        async with lock:
            running -= 1

    if coordinated:
        shared = _make_shared_redis()
        coords = [DistributedRepairCoordinator(client=shared, max_global_concurrent=GLOBAL_CAP,
                                               namespace="uasr:e8", lease_ms=10000)
                  for _ in range(N)]
        aws = []
        for ci, c in enumerate(coords):
            for j in range(per_node_crit):
                aws.append(c.submit(f"n{ci}_s{j}", DriftSeverity.CRITICAL, repair))
        await asyncio.gather(*aws)
    else:
        scheds = [RepairScheduler(max_concurrent=GLOBAL_CAP) for _ in range(N)]
        for s in scheds:
            await s.start()
        aws = []
        for si, s in enumerate(scheds):
            for j in range(per_node_crit):
                aws.append(s.submit(f"n{si}_s{j}", DriftSeverity.CRITICAL, repair))
        await asyncio.gather(*aws)
        for s in scheds:
            await s.stop(drain=True)
    return peak


def exp_repair_bounded(node_counts=(1, 2, 4, 8, 16)):
    rows = []
    for N in node_counts:
        peak_coord = asyncio.run(_fleet_repair(N, coordinated=True))
        peak_uncoord = asyncio.run(_fleet_repair(N, coordinated=False))
        rows.append({"experiment": "repair_bounded", "n_nodes": N,
                     "global_cap": GLOBAL_CAP,
                     "coordinated_peak_concurrent": peak_coord,
                     "uncoordinated_peak_concurrent": peak_uncoord,
                     "coordinated_stays_bounded": int(peak_coord <= GLOBAL_CAP)})
    return rows


def main():
    det = exp_detection_scaleout()
    rep = exp_repair_bounded()
    summarize("E8", det + rep, RESULTS_DIR / "exp8_horizontal_scale.csv")
    print("=== E8 Horizontal scaling (fleet of N nodes, shared Redis) ===")
    print("(a) detection scale-out:")
    for r in det:
        print(f"  N={r['n_nodes']:<3}: {r['aggregate_throughput_batch_per_s']:>9.1f} batch/s "
              f"({r['scaleout_factor']:.2f}x)")
    print(f"(b) repair global bound (cap={GLOBAL_CAP}):")
    for r in rep:
        print(f"  N={r['n_nodes']:<3}: coordinated peak={r['coordinated_peak_concurrent']:>3} "
              f"| uncoordinated peak={r['uncoordinated_peak_concurrent']:>3} "
              f"{'OK' if r['coordinated_stays_bounded'] else 'FAIL'}")
    return det + rep


if __name__ == "__main__":
    main()
