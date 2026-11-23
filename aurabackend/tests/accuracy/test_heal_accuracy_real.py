"""ADR-002 accuracy gate on REAL data (BUG-577).

The synthetic lane (``test_heal_accuracy.py``) measures the healer on datasets the
same work generated, so its result is no evidence for a customer's data. This lane
runs the same fault injectors, the same classification (CORRECT / QUARANTINED /
WRONG, a missed fault is WRONG) and the same gates on real datasets already on disk
(``realdata.REAL_SOURCES``: the COMPAS extract and the datasets sklearn and
statsmodels bundle -- nothing is downloaded), each split per trial by deterministic
row sampling into a baseline and a disjoint batch:

* (a) applied-fix precision >= 0.999 overall and per fault type: every batch where
  a fault was detected and rows were returned (``harness`` docstring). (b) the strict
  outcome accuracy, silent misses included, is printed per fault type and dataset
  and written to the report, not gated;
* zero false alarms on clean batches of every source;
* row accounting on every trial.

Results are printed per real dataset. ``AURA_HEAL_BENCH_REAL_TRIALS`` trials per
(dataset, fault type) run; by default a tenth of ``AURA_HEAL_BENCH_TRIALS`` (at
least 3). A failure replays with ``faults.build_trial(fault, seed, source)``.
Runs only with ``-m heal_realism`` (``conftest.py`` says why).
"""
from __future__ import annotations

import json
import os

import pytest

from .faults import FAULT_TYPES, NotApplicable, build_clean, build_trial
from .harness import (
    WRONG,
    applied,
    by_fault,
    classify,
    classify_clean,
    failing_applied,
    failing_classes,
    heal,
    metrics,
    report,
    report_path,
    run_isolated,
    table,
)
from .realdata import REAL_SOURCES

pytestmark = pytest.mark.heal_realism

TRIALS = int(os.getenv("AURA_HEAL_BENCH_REAL_TRIALS")
             or max(3, int(os.getenv("AURA_HEAL_BENCH_TRIALS", "50")) // 10))
CLEAN_TRIALS = 2 * TRIALS
PRECISION_GATE = 0.999


async def _run_all():
    faults, clean, skipped = [], [], []
    for source in REAL_SOURCES:
        for fault in FAULT_TYPES:
            for seed in range(TRIALS):
                try:
                    trial = build_trial(fault, seed, source)
                except NotApplicable:
                    skipped.append((source, fault))
                    continue
                faults.append(classify(trial, await heal(trial)))
        for seed in range(CLEAN_TRIALS):
            trial = build_clean(seed, source)
            clean.append(classify_clean(trial, await heal(trial)))
    return faults, clean, skipped


def _not_applicable(skipped) -> str:
    counts = {}
    for key in skipped:
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{s}/{f} x{n}" for (s, f), n in sorted(counts.items())) or "none"


@pytest.fixture(scope="module")
def real_bench():
    (faults, clean, skipped), elapsed = run_isolated(_run_all)
    print(f"\nADR-002 heal accuracy on REAL data, {TRIALS} trials per (dataset, fault type), "
          f"{len(REAL_SOURCES)} datasets, {elapsed:.0f}s\n"
          + table(faults, clean) + "\n"
          + f"not applicable (dataset/fault): {_not_applicable(skipped)}\n"
          + "(a) failures:\n" + (failing_applied(faults) or "none") + "\nevery WRONG trial:\n"
          + (failing_classes(faults + clean) or "no failing case"))
    out = report_path("real")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report(faults, clean) | {
                "trials_per_dataset_fault": TRIALS, "seconds": round(elapsed, 1),
                "per_dataset": {
                    s: vars(m) | {"precision": m.precision, "recall": m.recall}
                    for s in REAL_SOURCES
                    if (m := metrics([r for r in faults if r.trial.dataset.name == s])).trials},
                "clean_per_dataset": {
                    s: {"trials": len(c), "false_alarms": sum(1 for r in c if r.false_alarm)}
                    for s in REAL_SOURCES
                    if (c := [r for r in clean if r.trial.dataset.name == s])},
                "not_applicable": sorted({f"{s}/{f}" for s, f in skipped}),
            }, fh, indent=1)
    return faults, clean


def test_every_real_dataset_and_fault_type_ran(real_bench):
    faults, clean = real_bench
    assert {r.trial.dataset.name for r in clean} == set(REAL_SOURCES)
    assert {r.trial.dataset.name for r in faults} == set(REAL_SOURCES)
    assert set(by_fault(faults)) == set(FAULT_TYPES)


def test_row_accounting_is_never_violated_on_real_data(real_bench):
    faults, clean = real_bench
    broken = [r for r in faults + clean if r.accounting_error]
    assert not broken, "\n" + "\n".join(
        f"{r.trial.dataset.name} {r.trial.fault} seed={r.trial.seed}: {r.accounting_error}"
        for r in broken[:20])


def test_zero_false_alarms_on_clean_real_data(real_bench):
    _, clean = real_bench
    alarms = [r for r in clean if r.false_alarm or r.accounting_error]
    assert not alarms, f"{len(alarms)}/{len(clean)} clean real batches alarmed:\n" + failing_classes(alarms)


def test_overall_applied_fix_precision_on_real_data(real_bench):
    faults, _ = real_bench
    a = applied(faults)
    assert a.precision >= PRECISION_GATE, (
        f"(a) applied-fix precision {a.precision:.4f} < {PRECISION_GATE} ({a.wrong} wrong of {a.batches}):\n"
        + failing_applied(faults))


@pytest.mark.parametrize("fault", FAULT_TYPES)
def test_applied_fix_precision_per_fault_type_on_real_data(real_bench, fault):
    faults, _ = real_bench
    mine = [r for r in faults if r.trial.fault == fault]
    a = applied(mine)
    assert a.precision >= PRECISION_GATE, (
        f"{fault}: (a) applied-fix precision {a.precision:.4f} < {PRECISION_GATE} "
        f"({a.wrong} wrong of {a.batches}):\n" + failing_applied(mine))


# ── Replays of real-lane findings (moved from tests/test_heal_accuracy_bug577.py) ──
# They rebuild real-dataset trials, so they live with the lane; each runs alone on an
# isolated database (run_isolated), outside the lane's module fixture.


def _replay(make):
    async def go():
        trial = make()
        return trial, await heal(trial)
    return run_isolated(go)[0]


@pytest.mark.parametrize("seed, source", [(37, "sk_wine"), (88, "sm_modechoice")])
def test_the_lanes_clean_batches_raise_nothing(seed, source):
    # 8793a3b: the empty under/overflow bins priced with the chi-square quantile.
    trial, out = _replay(lambda: build_clean(seed, source))
    result = classify_clean(trial, out)
    assert result.false_alarm is None, result.why


@pytest.mark.parametrize("seed", [5, 21, 40, 41, 47])
def test_the_lanes_compas_x2_5_batches_return_no_wrong_value(seed):
    # BUG-577: whole-valued x2.5 counts cast back to int were returned as the truth.
    trial, out = _replay(lambda: build_trial("decoy_x2.5", seed, "compas"))
    result = classify(trial, out)
    assert result.outcome != WRONG, result.why


@pytest.mark.parametrize("seed", [13, 19])
def test_the_lanes_sk_digits_rename_and_x10_4_combos_return_no_wrong_value(seed):
    # BUG-577: one non-zero pixel x10^4 beside a proven rename came back healed.
    trial, out = _replay(lambda: build_trial("combo", seed, "sk_digits"))
    result = classify(trial, out)
    assert result.outcome != WRONG, result.why
