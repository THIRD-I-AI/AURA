"""ADR-002 accuracy gate in steady state: many batches per source (BUG-577).

Each stream (``steady.py``) registers a baseline, sends 5-10 clean batches through
/uasr/heal, injects a fault at a later batch, follows a repair it deployed through
the canary (kept after N batches, or rolled back, blocked, and re-proven once the
block expires in simulated time), then sends clean batches again. The gates are the
other lanes' gates over every batch of every stream, plus the canary's:

* (a) applied-fix precision >= 0.999 over every fault-carrying batch where a fault
  was detected and rows returned (harness docstring); (b) the strict outcome accuracy,
  silent misses included, is printed and written to the report, not gated;
* zero false alarms on every clean batch -- before the fault, through warm-up and
  past it, and after the fault;
* row accounting on every batch;
* every repair a keep scenario deployed is kept after the canary's N batches, and
  every rollback scenario rolls the repair back, holds the blocked fault, and
  proves the repair again after the block expires.

``AURA_HEAL_BENCH_STEADY_TRIALS`` streams run; by default a fifth of
``AURA_HEAL_BENCH_TRIALS`` (at least 28, two per fault type). Half stream synthetic
datasets, half real ones (``steady.steady_sources()``). A failure replays with
``steady.run_stream(seed)``. Runs only with ``-m heal_realism`` (``conftest.py``
says why).
"""
from __future__ import annotations

import json
import os
from collections import Counter

import pytest

from .faults import FAULT_TYPES
from .harness import applied, failing_applied, failing_classes, report, report_path, run_isolated, table
from .steady import StreamResult, run_stream, steady_sources

pytestmark = pytest.mark.heal_realism

STREAMS = int(os.getenv("AURA_HEAL_BENCH_STEADY_TRIALS")
              or max(2 * len(FAULT_TYPES), int(os.getenv("AURA_HEAL_BENCH_TRIALS", "50")) // 5))
PRECISION_GATE = 0.999


async def _run_all() -> StreamResult:
    total = StreamResult([], [], [])
    for seed in range(STREAMS):
        one = await run_stream(seed)
        total.faults += one.faults
        total.clean += one.clean
        total.scenarios += one.scenarios
    return total


@pytest.fixture(scope="module")
def steady_bench() -> StreamResult:
    run, elapsed = run_isolated(_run_all)
    kinds = Counter((s.kind, s.ok) for s in run.scenarios)
    failed = [s for s in run.scenarios if not s.ok]
    print(f"\nADR-002 heal accuracy in steady state, {STREAMS} streams "
          f"({len(run.faults)} fault batches, {len(run.clean)} clean batches; real sources: "
          f"{', '.join(steady_sources())}), {elapsed:.0f}s\n"
          + table(run.faults, run.clean) + "\n"
          + "canary scenarios: " + ", ".join(f"{k} {'ok' if ok else 'FAILED'} {n}"
                                             for (k, ok), n in sorted(kinds.items())) + "\n"
          + "".join(f"  seed={s.seed} {s.kind} {s.fault} [{s.dataset}]: {s.why}\n" for s in failed)
          + "(a) failures:\n" + (failing_applied(run.faults) or "none") + "\nevery WRONG trial:\n"
          + (failing_classes(run.faults + run.clean) or "no failing case"))
    out = report_path("steady")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report(run.faults, run.clean) | {
                "streams": STREAMS, "seconds": round(elapsed, 1),
                "scenarios": [vars(s) for s in run.scenarios]}, fh, indent=1)
    return run


def test_every_stream_carried_a_fault_and_both_canary_scenarios_ran(steady_bench):
    run = steady_bench
    assert len({r.trial.seed for r in run.clean}) == STREAMS
    kinds = {s.kind for s in run.scenarios}
    assert kinds == {"keep", "rollback"}, kinds


def test_row_accounting_is_never_violated_in_steady_state(steady_bench):
    broken = [r for r in steady_bench.faults + steady_bench.clean if r.accounting_error]
    assert not broken, "\n" + "\n".join(
        f"seed={r.trial.seed} {r.trial.fault}: {r.accounting_error}" for r in broken[:20])


def test_zero_false_alarms_across_every_clean_batch_of_every_stream(steady_bench):
    alarms = [r for r in steady_bench.clean if r.false_alarm or r.accounting_error]
    assert not alarms, (f"{len(alarms)}/{len(steady_bench.clean)} clean batches alarmed:\n"
                        + failing_classes(alarms))


def test_applied_fix_precision_over_every_fault_batch_in_steady_state(steady_bench):
    a = applied(steady_bench.faults)
    assert a.precision >= PRECISION_GATE, (
        f"(a) applied-fix precision {a.precision:.4f} < {PRECISION_GATE} ({a.wrong} wrong of {a.batches}):\n"
        + failing_applied(steady_bench.faults))


def test_kept_repairs_are_kept_and_rolled_back_repairs_are_blocked_then_re_proven(steady_bench):
    failed = [s for s in steady_bench.scenarios if not s.ok]
    assert not failed, "\n".join(f"seed={s.seed} {s.kind} {s.fault} [{s.dataset}]: {s.why}" for s in failed)
