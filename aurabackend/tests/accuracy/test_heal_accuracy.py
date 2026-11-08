"""ADR-002 accuracy gate: precision of /uasr/heal over injected faults with ground truth.

ADR-002 decision 2: 99.9-100% of applied fixes are correct, never guess, zero false
alarms on clean data. This module measures it on the real handler:

* precision = CORRECT / (CORRECT + WRONG) per fault type and overall, >= 0.999.
  A fault that was never detected and came back unchanged as healed is WRONG
  (BUG-571, BUG-572), so a miss lowers precision rather than vanishing from it.
* zero false alarms on the clean reference set (several batch sizes, with and
  without a schema snapshot, complete runs and micro-batches);
* row accounting on every trial: returned + quarantined = sent, each row once.

Recall (CORRECT / trials) is printed, not gated: abstaining lowers recall, never
precision (ADR-002 "What it does to recall").

The base lane runs ``AURA_HEAL_BENCH_TRIALS`` (default 50) trials per fault type; the
``heal-accuracy`` CI lane sets it to 500. Each trial is rebuilt from (fault, seed),
so a failure listed below replays with ``faults.build_trial(fault, seed)``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid

import pytest

import metadata_store.db as _metadata_db
from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.db import init_uasr_db
from uasr.reflector_agent import DiagnosticReflectorAgent

from .faults import FAULT_TYPES, NotApplicable, build_clean, build_trial
from .harness import by_fault, classify, classify_clean, failing_classes, heal, metrics, report, table

pytestmark = pytest.mark.heal_accuracy

TRIALS = int(os.getenv("AURA_HEAL_BENCH_TRIALS", "50"))
CLEAN_TRIALS = 2 * TRIALS
PRECISION_GATE = 0.999


async def _no_llm(self, *a, **k):
    return None


async def _run_all():
    await init_uasr_db()
    faults, clean = [], []
    for fault in FAULT_TYPES:
        for seed in range(TRIALS):
            try:
                trial = build_trial(fault, seed)
            except NotApplicable:
                continue
            faults.append(classify(trial, await heal(trial)))
    for seed in range(CLEAN_TRIALS):
        trial = build_clean(seed)
        clean.append(classify_clean(trial, await heal(trial)))
    engine = _metadata_db._engine
    if engine is not None:
        await engine.dispose()  # BUG-008: an undisposed aiosqlite engine hangs pytest at exit
    return faults, clean


@pytest.fixture(scope="module")
def bench():
    """Every trial, once per module, on an isolated SQLite file and with no LLM: the
    proposal path an unproven fault takes would otherwise reach the network."""
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    path = os.path.join(tempfile.gettempdir(), f"aura_heal_bench_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    mp = pytest.MonkeyPatch()
    mp.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    mp.setattr(DiagnosticReflectorAgent, "_llm_diagnosis", _no_llm)
    # Thousands of heals log every recovery step; the table below is the output that matters.
    uasr_log = logging.getLogger("uasr")
    log_level = uasr_log.level
    uasr_log.setLevel(logging.CRITICAL)
    started = time.monotonic()
    try:
        faults, clean = asyncio.run(_run_all())
    finally:
        uasr_log.setLevel(log_level)
        mp.undo()
        _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
        try:
            os.remove(path)
        except OSError:
            pass
    elapsed = time.monotonic() - started
    print(f"\nADR-002 heal accuracy, {TRIALS} trials per fault type, {elapsed:.0f}s\n"
          + table(faults, clean) + "\n" + (failing_classes(faults + clean) or "no failing case"))
    out = os.getenv("AURA_HEAL_BENCH_REPORT")
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


def test_overall_precision(bench):
    faults, _ = bench
    m = metrics(faults)
    assert m.precision >= PRECISION_GATE, (
        f"precision {m.precision:.4f} < {PRECISION_GATE} ({m.wrong} WRONG of {m.trials}):\n"
        + failing_classes(faults))


@pytest.mark.parametrize("fault", FAULT_TYPES)
def test_precision_per_fault_type(bench, fault):
    faults, _ = bench
    mine = [r for r in faults if r.trial.fault == fault]
    m = metrics(mine)
    assert m.precision >= PRECISION_GATE, (
        f"{fault}: precision {m.precision:.4f} < {PRECISION_GATE} ({m.wrong} WRONG of {m.trials}):\n"
        + failing_classes(mine))
