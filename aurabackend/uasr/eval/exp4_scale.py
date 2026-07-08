"""E4 — Scale & concurrency. Can one detector heal MANY pipelines at once with
bounded memory?

  (a) Throughput & p95 latency vs concurrent source count N (round-robin one
      batch per source, as a shared detector would see them).
  (b) Bounded state: with InMemoryStateStore(capacity=C), registering N>>C
      sources must hold exactly C and evict the rest (LRU) — the horizontal-
      scaling property from Bottleneck #3. Unbounded default holds all N.
  (c) Per-batch detect() cost after vectorization, vs batch size.
"""
from __future__ import annotations

import time
import tracemalloc

import numpy as np

from uasr.drift_detector import DriftDetector
from uasr.eval._harness import RESULTS_DIR, healthy_numeric, make_batch, summarize
from uasr.state_store import InMemoryStateStore


def exp_throughput(source_counts=(1, 8, 32, 128, 512), batches_per_source=5, n=500):
    rows = []
    for N in source_counts:
        rng = np.random.default_rng(42)
        d = DriftDetector(default_zeta=0.15)
        for sid in range(N):
            d.register_baseline(f"s{sid}", make_batch(f"s{sid}", "base",
                                                      {"v": healthy_numeric(rng, n=n)}))
        lat = []
        t0 = time.perf_counter()
        for _ in range(batches_per_source):
            for sid in range(N):
                b = make_batch(f"s{sid}", "x", {"v": healthy_numeric(rng, n=n)})
                ts = time.perf_counter()
                d.detect(b)
                lat.append((time.perf_counter() - ts) * 1e3)
        wall = time.perf_counter() - t0
        arr = np.array(lat)
        rows.append({"experiment": "throughput", "n_sources": N,
                     "total_batches": len(lat),
                     "throughput_batch_per_s": round(len(lat) / wall, 1),
                     "median_latency_ms": round(float(np.median(arr)), 3),
                     "p95_latency_ms": round(float(np.percentile(arr, 95)), 3)})
    return rows


def exp_bounded_state(N=5000, capacity=500, n=200):
    rng = np.random.default_rng(7)
    # bounded
    bounded = DriftDetector(default_zeta=0.15, state_store=InMemoryStateStore(capacity=capacity))
    tracemalloc.start()
    for sid in range(N):
        bounded.register_baseline(f"s{sid}", make_batch(f"s{sid}", "base",
                                                        {"v": healthy_numeric(rng, n=n)}))
    _, peak_bounded = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    held_bounded = len(list(bounded._store.source_ids()))
    # unbounded
    rng = np.random.default_rng(7)
    unb = DriftDetector(default_zeta=0.15, state_store=InMemoryStateStore(capacity=None))
    tracemalloc.start()
    for sid in range(N):
        unb.register_baseline(f"s{sid}", make_batch(f"s{sid}", "base",
                                                    {"v": healthy_numeric(rng, n=n)}))
    _, peak_unb = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    held_unb = len(list(unb._store.source_ids()))
    return {"experiment": "bounded_state", "N_registered": N, "capacity": capacity,
            "bounded_held": held_bounded, "bounded_evicted": N - held_bounded,
            "bounded_peak_mb": round(peak_bounded / 1e6, 2),
            "unbounded_held": held_unb, "unbounded_peak_mb": round(peak_unb / 1e6, 2),
            "bounded_holds_exactly_capacity": int(held_bounded == capacity)}


def exp_detect_cost(sizes=(1000, 5000, 20000, 100000, 200000), reps=5):
    rows = []
    for sz in sizes:
        rng = np.random.default_rng(11)
        d = DriftDetector(default_zeta=0.15)
        d.register_baseline("s", make_batch("s", "base", {"v": healthy_numeric(rng, n=sz)}))
        ts = []
        for _ in range(reps):
            b = make_batch("s", "x", {"v": healthy_numeric(rng, n=sz)})
            t0 = time.perf_counter(); d.detect(b); ts.append((time.perf_counter() - t0) * 1e3)
        rows.append({"experiment": "detect_cost", "batch_rows": sz,
                     "detect_ms_median": round(float(np.median(ts)), 3)})
    return rows


def main():
    tp = exp_throughput()
    bs = exp_bounded_state()
    dc = exp_detect_cost()
    all_rows = tp + [bs] + dc
    summarize("E4", all_rows, RESULTS_DIR / "exp4_scale.csv")
    print("=== E4 Scale & concurrency ===")
    for r in tp:
        print(f"  N={r['n_sources']:<4}: {r['throughput_batch_per_s']:>7.1f} batch/s  "
              f"p95={r['p95_latency_ms']:.2f}ms")
    print(f"bounded state: registered {bs['N_registered']}, cap {bs['capacity']} -> "
          f"held {bs['bounded_held']} evicted {bs['bounded_evicted']} "
          f"(peak {bs['bounded_peak_mb']}MB) | unbounded held {bs['unbounded_held']} "
          f"(peak {bs['unbounded_peak_mb']}MB)")
    for r in dc:
        print(f"  detect {r['batch_rows']:>7} rows: {r['detect_ms_median']:.2f}ms")
    return all_rows


if __name__ == "__main__":
    main()
