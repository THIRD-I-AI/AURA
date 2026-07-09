"""E7 — Vertical scaling. On a SINGLE host, does adding repair parallelism
(more cores/workers behind one RepairScheduler) increase healing throughput
while keeping the critical-repair response bounded?

The knob is ``RepairScheduler(max_concurrent=P)`` — how many recoveries run at
once on this box.  We submit a fixed flood of repairs (a realistic severity mix,
CRITICAL rare and arriving late behind a LOW-priority backlog) and measure, as
P grows 1→16:

  (a) aggregate healing throughput  (repairs completed / second)
  (b) makespan                       (wall time to drain the whole flood)
  (c) CRITICAL-repair response time  (submit → start) — must stay low even as
      the LOW backlog grows, because the scheduler admits by severity.

Each "repair" is a fixed-cost async sleep (a synthesis/validation stand-in) so
the measurement isolates the scheduler's admission behaviour from detector cost.
This is the *vertical* axis: one process, more concurrency.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np

from uasr.eval._harness import RESULTS_DIR, summarize
from uasr.models import DriftSeverity
from uasr.repair_scheduler import RepairScheduler

REPAIR_COST_S = 0.02  # fixed per-repair work (synthesis/validation stand-in)


async def _run_flood(max_concurrent: int, n_low: int, n_crit: int, seed: int = 0):
    """Submit n_low LOW repairs, then n_crit CRITICAL repairs arriving after the
    backlog is already queued. Returns (throughput, makespan, crit_response_p95).
    """
    sched = RepairScheduler(max_concurrent=max_concurrent)
    await sched.start()

    start = {}
    done = {}
    submit_t = {}

    async def repair(tag: str):
        start[tag] = time.perf_counter()
        await asyncio.sleep(REPAIR_COST_S)
        done[tag] = time.perf_counter()

    t0 = time.perf_counter()
    # LOW backlog submitted first — this is the flood a CRITICAL must cut through
    low_aws = []
    for i in range(n_low):
        tag = f"low{i}"
        submit_t[tag] = time.perf_counter()
        low_aws.append(sched.submit(f"src_low{i}", DriftSeverity.LOW,
                                    lambda t=tag: repair(t)))
    # CRITICAL repairs arrive slightly later, after the backlog is enqueued
    await asyncio.sleep(0.001)
    crit_aws = []
    for i in range(n_crit):
        tag = f"crit{i}"
        submit_t[tag] = time.perf_counter()
        crit_aws.append(sched.submit(f"src_crit{i}", DriftSeverity.CRITICAL,
                                     lambda t=tag: repair(t)))

    await asyncio.gather(*low_aws, *crit_aws)
    makespan = time.perf_counter() - t0
    await sched.stop(drain=True)

    total = n_low + n_crit
    throughput = total / makespan
    crit_resp = [(start[f"crit{i}"] - submit_t[f"crit{i}"]) * 1e3 for i in range(n_crit)]
    return {
        "throughput_repairs_per_s": round(throughput, 1),
        "makespan_s": round(makespan, 3),
        "crit_response_ms_median": round(float(np.median(crit_resp)), 1),
        "crit_response_ms_p95": round(float(np.percentile(crit_resp, 95)), 1),
        "max_observed_concurrency": sched.stats.max_observed_concurrency,
    }


def exp_vertical(parallelisms=(1, 2, 4, 8, 16), n_low=200, n_crit=8):
    rows = []
    ideal_base = None
    for p in parallelisms:
        r = asyncio.run(_run_flood(p, n_low, n_crit))
        if ideal_base is None:
            ideal_base = r["throughput_repairs_per_s"]
        r_full = {"experiment": "vertical_scale", "parallelism": p,
                  "n_repairs": n_low + n_crit, **r,
                  "throughput_speedup": round(r["throughput_repairs_per_s"] / ideal_base, 2)}
        rows.append(r_full)
    return rows


def main():
    rows = exp_vertical()
    summarize("E7", rows, RESULTS_DIR / "exp7_vertical_scale.csv")
    print("=== E7 Vertical scaling (one host, repair parallelism P) ===")
    print(f"{'P':>3} {'throughput/s':>13} {'speedup':>8} {'makespan':>9} "
          f"{'crit_resp_med':>13} {'crit_p95':>9} {'max_conc':>9}")
    for r in rows:
        print(f"{r['parallelism']:>3} {r['throughput_repairs_per_s']:>13.1f} "
              f"{r['throughput_speedup']:>7.2f}x {r['makespan_s']:>8.3f}s "
              f"{r['crit_response_ms_median']:>11.1f}ms {r['crit_response_ms_p95']:>7.1f}ms "
              f"{r['max_observed_concurrency']:>9}")
    return rows


if __name__ == "__main__":
    main()
