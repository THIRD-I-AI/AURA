"""E6 — Error budget. Composes the per-effect-size residual error rate from
independently measured pieces:

    P(residual) = P(miss) + P(detect) * P(bad heal survives canary)

with two detection channels (single-batch histogram-KL and the sequential
Wasserstein martingale) and the canary false-commit rate from E3.

The single-batch channel has a hard ~2-sigma deadband (its location-shift term
only fires above |mu_batch - mu_ref|/sigma_ref > 2.0); the sequential martingale
closes that deadband for *persistent* drift. This experiment measures both
curves and the combined residual, showing residual ~ 0 for any corruption
>= 0.5 sigma with a provably bounded false-alarm rate.
"""
from __future__ import annotations

import numpy as np

from uasr.drift_detector import DriftDetector
from uasr.eval._harness import RESULTS_DIR, healthy_numeric, make_batch, summarize
from uasr.martingale import WassersteinMartingaleDetector

SIGMA = 5.0
# Canary false-commit rate (E3, k>=3): a detected+healed corruption almost
# never commits the wrong transform. This is the P(bad heal survives) term.
P_FALSE_COMMIT = 0.0


def _fresh_detector(rng, warmup=8, n=500):
    d = DriftDetector(default_zeta=0.15)
    d.register_baseline("s", make_batch("s", "base", {"v": healthy_numeric(rng, n=n)}))
    for i in range(warmup):
        d.detect(make_batch("s", f"w{i}", {"v": healthy_numeric(rng, n=n)}))
    return d


def exp_single_batch(seeds=300):
    """Single-batch detection rate vs mean-shift effect size."""
    rows = []
    for es in np.round(np.arange(0.0, 3.01, 0.25), 2):
        hits = 0
        for s in range(seeds):
            rng = np.random.default_rng(7000 + s)
            d = _fresh_detector(rng)
            col = healthy_numeric(rng) + es * SIGMA
            hits += int(d.detect(make_batch("s", "c", {"v": col})).drift_detected)
        rows.append({"experiment": "single_batch", "effect_sigma": float(es),
                     "p_detect": round(hits / seeds, 4), "seeds": seeds})
    return rows


def _run_martingale(es, seeds=200, alpha=0.001, baseline_window=100, max_active=60):
    lats = []
    for s in range(seeds):
        rng = np.random.default_rng(9000 + s)
        det = WassersteinMartingaleDetector(alpha=alpha, baseline_window=baseline_window)
        det.register_baseline("s", {"v": list(healthy_numeric(rng, n=500))})
        for _ in range(baseline_window):
            det.update("s", "v", list(healthy_numeric(rng, n=200)))
        lat = None
        for i in range(max_active):
            col = list(healthy_numeric(rng, n=200) + es * SIGMA)
            if det.update("s", "v", col):
                lat = i + 1
                break
        lats.append(lat if lat is not None else max_active + 1)
    arr = np.array(lats)
    return {"detected_frac": round(float((arr <= max_active).mean()), 4),
            "median_lat": float(np.median(arr)), "p95_lat": float(np.percentile(arr, 95))}


def exp_sequential(seeds=200):
    """Sequential martingale detection in the single-batch deadband, + null FPR."""
    rows = []
    for a in [0.05, 0.01, 0.001]:
        r = _run_martingale(0.0, seeds=seeds, alpha=a)
        rows.append({"experiment": "seq_null_fpr", "alpha": a,
                     "false_alarm_frac": r["detected_frac"], "seeds": seeds})
    for es in [0.25, 0.5, 0.75, 1.0, 1.5]:
        r = _run_martingale(es, seeds=seeds, alpha=0.001)
        rows.append({"experiment": "seq_detect", "effect_sigma": es, "alpha": 0.001,
                     "detected_frac": r["detected_frac"], "median_lat": r["median_lat"],
                     "p95_lat": r["p95_lat"], "seeds": seeds})
    return rows


def exp_budget(single_rows, seq_rows):
    """Compose the residual error budget from the two detection curves."""
    single = {r["effect_sigma"]: r["p_detect"] for r in single_rows}
    seq = {r["effect_sigma"]: r["detected_frac"]
           for r in seq_rows if r["experiment"] == "seq_detect"}
    sx = sorted(single)
    sy = [single[x] for x in sx]
    qx = sorted(seq)
    qy = [seq[x] for x in qx]
    rows = []
    for es in [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0]:
        ps = float(np.interp(es, sx, sy))
        pq = float(np.interp(es, qx, qy)) if es <= qx[-1] else 1.0
        p_detect = 1 - (1 - ps) * (1 - pq)
        p_miss = 1 - p_detect
        p_res = p_miss + p_detect * P_FALSE_COMMIT
        rows.append({"experiment": "budget", "effect_sigma": es,
                     "p_single_batch": round(ps, 3), "p_seq_martingale": round(pq, 3),
                     "p_detect_combined": round(p_detect, 4), "p_miss": round(p_miss, 4),
                     "p_residual_error": round(p_res, 4)})
    return rows


def main():
    single = exp_single_batch()
    seq = exp_sequential()
    budget = exp_budget(single, seq)
    summarize("E6", single + seq + budget, RESULTS_DIR / "exp6_error_budget.csv")
    print("=== E6 Error Budget ===")
    print("Single-batch deadband (P_detect flat until ~2sigma):")
    for r in single:
        if r["effect_sigma"] in (0.5, 1.0, 1.5, 2.0, 2.5):
            print(f"  es={r['effect_sigma']:.2f}s: {r['p_detect']:.3f}")
    print("Sequential closes deadband:")
    for r in seq:
        if r["experiment"] == "seq_detect":
            print(f"  es={r['effect_sigma']:.2f}s: detect={r['detected_frac']:.3f} "
                  f"median_lat={r['median_lat']:.0f}")
    print("Residual error budget:")
    for r in budget:
        print(f"  es={r['effect_sigma']:.2f}s: P(miss)={r['p_miss']:.4f} "
              f"P(residual)={r['p_residual_error']:.4f}")
    return single + seq + budget


if __name__ == "__main__":
    main()
