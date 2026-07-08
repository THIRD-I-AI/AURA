"""E5 — End-to-end error reduction (the capstone). A realistic pipeline emits a
downstream metric (a business aggregate: mean of a priced column). A unit bug
hits upstream at a known batch; UASR must drive the downstream error back to
baseline and hold it there.

We measure, per batch, the relative error of the downstream aggregate vs the
true (uncorrupted) aggregate, under two regimes:
    - NO healing  (control): error stays at the corruption magnitude.
    - WITH healing (UASR):   error spikes then returns to ~0 after commit.

Reports: peak error, steady-state error after commit, time-to-recovery (batches),
and area-under-error-curve reduction (control vs UASR).
"""
from __future__ import annotations

import numpy as np

from uasr.eval._harness import RESULTS_DIR, summarize
from uasr.numeric_heal_controller import HealState, NumericHealController
from uasr.numeric_semantics import NumericBaseline


def _run(seed, factor=100.0, onset=6, total=24, mu=50.0, sigma=5.0, n=300, heal=True):
    rng = np.random.default_rng(seed)
    baseline = NumericBaseline.fit([rng.normal(mu, sigma, n) for _ in range(12)])
    ctrl = NumericHealController(k_confirm=3, revert_patience=3)
    ctrl.load_baseline("s", "price", baseline)
    true_agg = mu  # expected downstream aggregate (mean of priced column)
    errs = []
    recovered_at = None
    for i in range(total):
        raw = rng.normal(mu, sigma, n)
        if i >= onset:
            raw = raw * factor
        if heal:
            ctrl.observe("s", "price", raw)
            out = np.asarray(ctrl.apply("s", "price", raw), dtype=float)
        else:
            out = raw
        agg = float(np.mean(out))
        rel_err = abs(agg - true_agg) / abs(true_agg)
        errs.append(rel_err)
        if heal and i >= onset and rel_err < 0.02 and recovered_at is None:
            recovered_at = i - onset + 1
    return np.array(errs), recovered_at, onset


def main(seeds=100):
    rows = []
    peak_ctrl = peak_heal = ss_ctrl = ss_heal = 0.0
    ttr = []
    auc_ctrl = auc_heal = 0.0
    for s in range(seeds):
        e_ctrl, _, onset = _run(3000 + s, heal=False)
        e_heal, rec, _ = _run(3000 + s, heal=True)
        peak_ctrl += e_ctrl[onset:].max(); peak_heal += e_heal[onset:].max()
        ss_ctrl += e_ctrl[-5:].mean(); ss_heal += e_heal[-5:].mean()
        auc_ctrl += e_ctrl[onset:].sum(); auc_heal += e_heal[onset:].sum()
        if rec is not None:
            ttr.append(rec)
        rows.append({"seed": s, "peak_ctrl": round(float(e_ctrl[onset:].max()), 4),
                     "peak_heal": round(float(e_heal[onset:].max()), 4),
                     "ss_ctrl": round(float(e_ctrl[-5:].mean()), 4),
                     "ss_heal": round(float(e_heal[-5:].mean()), 6),
                     "recovered_at": rec if rec is not None else -1})
    summarize("E5", rows, RESULTS_DIR / "exp5_endtoend.csv")
    N = seeds
    ttr_arr = np.array(ttr) if ttr else np.array([np.nan])
    print("=== E5 End-to-end error reduction ===")
    print(f"seeds={N}  corruption=x100 at batch 6 of 24")
    print(f"peak error   : control={peak_ctrl/N:.3f}  UASR={peak_heal/N:.3f}")
    print(f"steady-state : control={ss_ctrl/N:.3f}  UASR={ss_heal/N:.6f}")
    print(f"AUC(error)   : control={auc_ctrl/N:.2f}  UASR={auc_heal/N:.2f}  "
          f"reduction={100*(1-auc_heal/auc_ctrl):.1f}%")
    print(f"time-to-recovery: median={np.median(ttr_arr):.1f} batches "
          f"(recovered {len(ttr)}/{N})")
    return {"peak_ctrl": peak_ctrl/N, "peak_heal": peak_heal/N,
            "ss_ctrl": ss_ctrl/N, "ss_heal": ss_heal/N,
            "auc_reduction_pct": 100*(1-auc_heal/auc_ctrl),
            "ttr_median": float(np.median(ttr_arr)), "recovered": len(ttr), "seeds": N}


if __name__ == "__main__":
    main()
