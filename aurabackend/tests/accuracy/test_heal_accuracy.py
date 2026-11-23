"""ADR-002 accuracy gate: precision of /uasr/heal over injected faults with ground truth.

ADR-002 decision 2: 99.9-100% of applied fixes are correct, never guess, zero false
alarms on clean data. This module measures it on the real handler:

* (a) applied-fix precision per fault type and overall, >= 0.999: every batch where
  a fault was detected and rows were returned must return only ground truth
  (ADR-002 "applied fixes whose output equals ground truth"; ``harness`` docstring).
* (b) strict outcome accuracy = CORRECT / (CORRECT + WRONG), printed and reported,
  not gated: a fault that was never detected and came back unchanged as healed is
  WRONG there (BUG-571, BUG-572), so a miss is counted rather than vanishing.
* zero false alarms on the clean reference set (several batch sizes, with and
  without a schema snapshot, complete runs and micro-batches);
* row accounting on every trial: returned + quarantined = sent, each row once;
* keyed duplicates (a declared key, ADR-002 "exact duplicates on the declared key"):
  0 misses, and each counts in (a);
* the miss floor (``accuracy_floor.json``): silent misses and missed alerts no
  higher, recall no lower, than recorded for this trial count. Lower the floor in
  the same change when detection improves; raising it is an owner's decision.

Recall (CORRECT / trials) is printed, not gated: abstaining lowers recall, never
precision (ADR-002 "What it does to recall").

The base lane runs ``AURA_HEAL_BENCH_TRIALS`` (default 50) trials per fault type; the
``heal-accuracy`` CI lane sets it to 500. Each trial is rebuilt from (fault, seed),
so a failure listed below replays with ``faults.build_trial(fault, seed)``.
"""
from __future__ import annotations

import json
import os

import pytest

from .faults import FAULT_TYPES, NotApplicable, build_clean, build_trial
from .harness import (
    applied,
    by_fault,
    classify,
    classify_clean,
    failing_applied,
    failing_classes,
    floor_failures,
    heal_run,
    keyed_misses,
    metrics,
    report,
    report_path,
    run_isolated,
    table,
)

pytestmark = pytest.mark.heal_accuracy

TRIALS = int(os.getenv("AURA_HEAL_BENCH_TRIALS", "50"))
CLEAN_TRIALS = 2 * TRIALS
PRECISION_GATE = 0.999


async def _run_all():
    faults, clean = [], []
    for fault in FAULT_TYPES:
        for seed in range(TRIALS):
            try:
                trial = build_trial(fault, seed)
            except NotApplicable:
                continue
            out, pages = await heal_run(trial)
            faults.append(classify(trial, out))
            clean += pages
    for seed in range(CLEAN_TRIALS):
        trial = build_clean(seed)
        out, _ = await heal_run(trial)
        clean.append(classify_clean(trial, out))
    return faults, clean


@pytest.fixture(scope="module")
def bench():
    """Every trial, once per module, on an isolated SQLite file and with no LLM
    (``run_isolated``)."""
    (faults, clean), elapsed = run_isolated(_run_all)
    print(f"\nADR-002 heal accuracy, {TRIALS} trials per fault type, {elapsed:.0f}s\n"
          + table(faults, clean) + "\n(a) failures:\n" + (failing_applied(faults) or "none")
          + "\nevery WRONG trial:\n" + (failing_classes(faults + clean) or "no failing case"))
    out = report_path("synthetic")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report(faults, clean) | {"trials_per_fault": TRIALS, "seconds": round(elapsed, 1)},
                      fh, indent=1)
    return faults, clean


def test_every_fault_type_ran_enough_trials(bench):
    faults, _ = bench
    counts = {f: m.trials for f, m in by_fault(faults).items()}
    assert set(counts) == set(FAULT_TYPES)
    assert all(n >= 0.9 * TRIALS for n in counts.values()), counts


def test_row_accounting_is_never_violated(bench):
    faults, clean = bench
    broken = [r for r in faults + clean if r.accounting_error]
    assert not broken, "\n" + "\n".join(
        f"{r.trial.fault} seed={r.trial.seed}: {r.accounting_error}" for r in broken[:20])


def test_zero_false_alarms_on_clean_data(bench):
    _, clean = bench
    alarms = [r for r in clean if r.false_alarm or r.accounting_error]
    assert not alarms, f"{len(alarms)}/{len(clean)} clean batches alarmed:\n" + failing_classes(alarms)


def test_overall_applied_fix_precision(bench):
    faults, _ = bench
    a = applied(faults)
    assert a.precision >= PRECISION_GATE, (
        f"(a) applied-fix precision {a.precision:.4f} < {PRECISION_GATE} ({a.wrong} wrong of {a.batches}):\n"
        + failing_applied(faults))


def test_no_keyed_duplicate_is_missed(bench):
    faults, _ = bench
    missed = keyed_misses(faults)
    assert not missed, f"{len(missed)} keyed duplicate(s) without the alert:\n" + failing_classes(missed)


def test_misses_and_recall_hold_the_floor(bench):
    faults, _ = bench
    problem = floor_failures("synthetic", str(TRIALS), faults)
    assert problem is None, problem


@pytest.mark.parametrize("fault", FAULT_TYPES)
def test_applied_fix_precision_per_fault_type(bench, fault):
    faults, _ = bench
    mine = [r for r in faults if r.trial.fault == fault]
    a = applied(mine)
    assert a.precision >= PRECISION_GATE, (
        f"{fault}: (a) applied-fix precision {a.precision:.4f} < {PRECISION_GATE} "
        f"({a.wrong} wrong of {a.batches}):\n" + failing_applied(mine))
