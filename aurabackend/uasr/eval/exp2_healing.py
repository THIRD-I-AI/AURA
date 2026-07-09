"""E2 — Healing correctness on a REAL numeric corpus.

Uses sklearn's bundled datasets (breast-cancer, wine, diabetes, iris) — real
numeric columns spanning ~3 orders of magnitude — as the ground-truth healthy
stream. For each column we:
  (a) fit a NumericBaseline from 12 healthy bootstrap batches,
  (b) inject a unit/scale bug, verify the controller COMMITS an inverse and
      ctrl.apply() returns values matching the original within +/-1%,
  (c) inject a legitimate regime change (mean shift), verify it is NEVER
      committed as a heal.

The paper's central safety property: heal the repairable, refuse the rest.
"""
from __future__ import annotations

import numpy as np

from uasr.eval._harness import RESULTS_DIR, summarize
from uasr.numeric_heal_controller import HealState, NumericHealController
from uasr.numeric_semantics import NumericBaseline


def _load_real_columns():
    from sklearn import datasets
    cols = {}
    for name, load in [("bc", datasets.load_breast_cancer), ("wine", datasets.load_wine),
                       ("diab", datasets.load_diabetes), ("iris", datasets.load_iris)]:
        X = load().data
        for j in range(X.shape[1]):
            c = X[:, j].astype(float)
            nz = c[c != 0]
            if np.std(c) > 0 and np.all(np.isfinite(c)) and nz.size and np.min(np.abs(nz)) > 0:
                cols[f"{name}_{j}"] = c
    return cols


UNIT_BUGS = {"x100": 100.0, "div100": 0.01, "sec_to_ms": 1000.0, "x1000_div": 0.001}


def main(seeds=3):
    cols = _load_real_columns()
    rows = []
    heal_ok = heal_tot = 0
    detect_ok = detect_tot = 0
    fp_heal = regime_tot = 0
    for cname, col in cols.items():
        for s in range(seeds):
            rng = np.random.default_rng(hash((cname, s)) % (2**32))
            healthy = [rng.choice(col, 200, replace=True) for _ in range(12)]
            baseline = NumericBaseline.fit(healthy)

            # (b) unit bugs: must commit + apply() heals within 1%
            for bug, factor in UNIT_BUGS.items():
                ctrl = NumericHealController(k_confirm=3, revert_patience=3)
                ctrl.load_baseline("src", "c", baseline)
                orig = rng.choice(col, 200, replace=True)
                committed = False
                heal_tot += 1
                for i in range(8):
                    raw = (rng.choice(col, 200, replace=True) if i == 0 else orig) * factor
                    dec = ctrl.observe("src", "c", raw)
                    detect_tot += 1
                    detect_ok += int(dec.raw_drifted)
                    if dec.state is HealState.COMMITTED and dec.applied_transform != "none":
                        committed = True
                        healed = np.asarray(ctrl.apply("src", "c", orig * factor), dtype=float)
                        rel = np.abs(healed - orig) / (np.abs(orig) + 1e-9)
                        within = float(np.mean(rel < 0.01))
                        heal_ok += int(within > 0.99)
                        rows.append({"column": cname, "seed": s, "bug": bug, "committed": 1,
                                     "heal_within_1pct": round(within, 4)})
                        break
                if not committed:
                    rows.append({"column": cname, "seed": s, "bug": bug, "committed": 0,
                                 "heal_within_1pct": 0.0})

            # (c) regime change: must NEVER commit a heal
            ctrl = NumericHealController(k_confirm=3, revert_patience=3)
            ctrl.load_baseline("src", "c", baseline)
            shift = 2.0 * float(np.std(col))
            regime_committed = False
            regime_tot += 1
            for i in range(8):
                raw = rng.choice(col, 200, replace=True) + shift
                dec = ctrl.observe("src", "c", raw)
                if dec.state is HealState.COMMITTED and dec.applied_transform != "none":
                    regime_committed = True
                    break
            fp_heal += int(regime_committed)

    summarize("E2", rows, RESULTS_DIR / "exp2_healing.csv")
    heal_rate = heal_ok / heal_tot
    fh_rate = fp_heal / regime_tot
    print("=== E2 Healing correctness (real corpus) ===")
    print(f"columns={len(cols)}  seeds={seeds}  unit-bug trials={heal_tot}  regime trials={regime_tot}")
    print(f"unit-bug commit+heal within 1%: {heal_ok}/{heal_tot} = {heal_rate:.4f}")
    print(f"unit-bug detection:             {detect_ok}/{detect_tot} = {detect_ok/detect_tot:.4f}")
    print(f"regime-change FALSE heal:       {fp_heal}/{regime_tot} = {fh_rate:.4f}")
    return {"heal_rate": heal_rate, "false_heal_rate": fh_rate, "n_columns": len(cols),
            "unit_trials": heal_tot, "regime_trials": regime_tot}


if __name__ == "__main__":
    main()
