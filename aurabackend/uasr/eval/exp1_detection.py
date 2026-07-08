"""E1 — Detection. Does UASR catch drift across channels with a controlled,
*reported* false-positive rate?

Three questions, three tables:
  (a) FPR: run many healthy batches after baseline; split cold-start (warmup)
      vs steady-state FP rate — the adaptive threshold self-calibrates so the
      honest number is steady-state, with warmup length reported alongside.
  (b) Detection rate vs magnitude: inject unit-scale corruption of growing
      factor; measure fraction of seeds detected at first corrupt batch.
  (c) Detection latency: batches from corruption onset to first alarm.
"""
from __future__ import annotations

import numpy as np

from uasr.drift_detector import DriftDetector
from uasr.eval._harness import RESULTS_DIR, healthy_numeric, make_batch, summarize
from uasr.models import BatchPayload


def _fresh_detector(rng, warmup=8, n=500):
    d = DriftDetector(default_zeta=0.15)
    base = make_batch("s", "base", {"v": healthy_numeric(rng, n=n)})
    d.register_baseline("s", base)
    # Feed `warmup` healthy batches so KL-history (>=5) calibrates zeta.
    for i in range(warmup):
        d.detect(make_batch("s", f"w{i}", {"v": healthy_numeric(rng, n=n)}))
    return d


def exp_fpr(seeds=200, healthy_batches=30):
    """Per-seed: count FP in first 5 (cold) vs remaining (steady)."""
    cold, steady, cold_n, steady_n = 0, 0, 0, 0
    for s in range(seeds):
        rng = np.random.default_rng(s)
        d = DriftDetector(default_zeta=0.15)
        d.register_baseline("s", make_batch("s", "base", {"v": healthy_numeric(rng)}))
        for i in range(healthy_batches):
            r = d.detect(make_batch("s", f"h{i}", {"v": healthy_numeric(rng)}))
            if i < 5:
                cold += int(r.drift_detected); cold_n += 1
            else:
                steady += int(r.drift_detected); steady_n += 1
    return {"experiment": "fpr", "cold_start_fpr": round(cold / cold_n, 4),
            "steady_state_fpr": round(steady / steady_n, 4),
            "cold_n": cold_n, "steady_n": steady_n, "seeds": seeds}


def exp_detection_rate(seeds=200):
    """Detection rate at first corrupt batch vs unit-scale factor."""
    rows = []
    for factor in [1.05, 1.1, 1.25, 1.5, 2.0, 10.0, 100.0]:
        hits = 0
        for s in range(seeds):
            rng = np.random.default_rng(1000 + s)
            d = _fresh_detector(rng)
            corrupt = make_batch("s", "c", {"v": healthy_numeric(rng) * factor})
            hits += int(d.detect(corrupt).drift_detected)
        rows.append({"experiment": "detection_rate", "unit_factor": factor,
                     "detection_rate": round(hits / seeds, 4), "seeds": seeds})
    return rows


def exp_latency(seeds=200, max_batches=20):
    """Batches from onset to first alarm for a moderate 1.5x scale bug."""
    lats = []
    for s in range(seeds):
        rng = np.random.default_rng(2000 + s)
        d = _fresh_detector(rng)
        lat = None
        for i in range(max_batches):
            r = d.detect(make_batch("s", f"c{i}", {"v": healthy_numeric(rng) * 1.5}))
            if r.drift_detected:
                lat = i + 1; break
        lats.append(lat if lat is not None else max_batches + 1)
    arr = np.array(lats)
    return {"experiment": "latency", "median_batches": float(np.median(arr)),
            "p95_batches": float(np.percentile(arr, 95)),
            "detected_within_window": round(float((arr <= max_batches).mean()), 4),
            "seeds": seeds}


def main():
    fpr = exp_fpr()
    det = exp_detection_rate()
    lat = exp_latency()
    all_rows = [fpr] + det + [lat]
    summarize("E1", all_rows, RESULTS_DIR / "exp1_detection.csv")
    print("=== E1 Detection ===")
    print(f"FPR  cold-start={fpr['cold_start_fpr']:.3f}  steady-state={fpr['steady_state_fpr']:.3f}")
    for r in det:
        print(f"  factor x{r['unit_factor']:<6}: detection={r['detection_rate']:.3f}")
    print(f"Latency median={lat['median_batches']} p95={lat['p95_batches']} "
          f"within-window={lat['detected_within_window']:.3f}")
    return all_rows


if __name__ == "__main__":
    main()
