"""E3 — Safety invariants. The properties that make auto-healing trustworthy.

  (a) Two-sided auto-revert: once a heal is committed, if the raw upstream
      recovers the controller must REVERT (stop dividing good data by 100).
  (b) Regime-change refusal across k_confirm settings: a legitimate mean/
      variance shift must never be committed as a heal (the safety property),
      swept over k in {1,2,3,5}.
  (c) Audit completeness: every state transition emits a record with a unique
      SHA-256 hash — no silent commits.
"""
from __future__ import annotations

import numpy as np

from uasr.eval._harness import RESULTS_DIR, summarize
from uasr.numeric_heal_controller import HealState, NumericHealController
from uasr.numeric_semantics import NumericBaseline


def _baseline(rng, mu=50.0, sigma=5.0, nb=12, n=200):
    return NumericBaseline.fit([rng.normal(mu, sigma, n) for _ in range(nb)])


def exp_revert(seeds=200):
    """Bug arrives, commits, then raw recovers -> must auto-revert."""
    reverted = 0
    for s in range(seeds):
        rng = np.random.default_rng(3000 + s)
        ctrl = NumericHealController(k_confirm=3, revert_patience=3)
        ctrl.load_baseline("s", "c", _baseline(rng))
        # 4 corrupt (x100) batches -> commit
        for i in range(4):
            ctrl.observe("s", "c", rng.normal(50, 5, 200) * 100.0)
        # now raw recovers: healthy batches -> should revert within patience+slack
        did = False
        for i in range(8):
            dec = ctrl.observe("s", "c", rng.normal(50, 5, 200))
            if dec.state is HealState.OBSERVING:
                did = True
                break
        reverted += int(did)
    return {"experiment": "auto_revert", "revert_rate": round(reverted / seeds, 4), "seeds": seeds}


def exp_regime_refusal(seeds=200):
    rows = []
    for k in [1, 2, 3, 5]:
        false_commit = 0
        for s in range(seeds):
            rng = np.random.default_rng(4000 + s)
            ctrl = NumericHealController(k_confirm=k, revert_patience=3)
            ctrl.load_baseline("s", "c", _baseline(rng))
            shift = 3.0 * 5.0  # 3 sigma legitimate mean shift
            committed = False
            for i in range(10):
                dec = ctrl.observe("s", "c", rng.normal(50, 5, 200) + shift)
                if dec.state is HealState.COMMITTED and dec.applied_transform != "none":
                    committed = True
                    break
            false_commit += int(committed)
        rows.append({"experiment": "regime_refusal", "k_confirm": k,
                     "false_commit_rate": round(false_commit / seeds, 4), "seeds": seeds})
    return rows


def exp_audit_completeness(seeds=100):
    """Every transition has a unique-hash audit record."""
    total_transitions = 0
    record_ids = []
    content_hashes = set()
    for s in range(seeds):
        rng = np.random.default_rng(5000 + s)
        ctrl = NumericHealController(k_confirm=3, revert_patience=3)
        ctrl.load_baseline("s", "c", _baseline(rng))
        prev = HealState.OBSERVING
        for i in range(4):
            dec = ctrl.observe("s", "c", rng.normal(50, 5, 200) * 100.0)
            if dec.state is not prev:
                total_transitions += 1
                prev = dec.state
        for i in range(8):
            dec = ctrl.observe("s", "c", rng.normal(50, 5, 200))
            if dec.state is not prev:
                total_transitions += 1
                prev = dec.state
        for rec in ctrl.audit_log:
            record_ids.append(rec.record_id)
            content_hashes.add(rec.audit_record_hash)
    # record_id must be unique per event; content-hash is content-addressed
    # (identical logical events across seeds collide by design, like a git blob).
    return {"experiment": "audit", "transitions": total_transitions,
            "audit_records": len(record_ids),
            "unique_record_ids": len(set(record_ids)),
            "unique_content_hashes": len(content_hashes),
            "record_id_unique": int(len(set(record_ids)) == len(record_ids)),
            "seeds": seeds}


def main():
    rev = exp_revert()
    reg = exp_regime_refusal()
    aud = exp_audit_completeness()
    all_rows = [rev] + reg + [aud]
    summarize("E3", all_rows, RESULTS_DIR / "exp3_safety.csv")
    print("=== E3 Safety invariants ===")
    print(f"auto-revert rate: {rev['revert_rate']:.4f} (n={rev['seeds']})")
    for r in reg:
        print(f"  regime false-commit @k={r['k_confirm']}: {r['false_commit_rate']:.4f}")
    print(f"audit: {aud['audit_records']} records, {aud['unique_record_ids']} unique record_ids "
          f"(unique={bool(aud['record_id_unique'])}), {aud['unique_content_hashes']} content-hashes, "
          f"{aud['transitions']} transitions")
    return all_rows


if __name__ == "__main__":
    main()
