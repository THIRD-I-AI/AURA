"""Run trials through the real /uasr/heal handler and score them (ADR-002 benchmark).

The handler is called in-process exactly as tests/test_heal_phase2a_repairs.py does:
the production DriftDetector, RecoveryLoop and RepairCanary of ``uasr.service``, a
real SQLite database, a baseline registered first, and no FastAPI lifespan. Every
trial gets its own source id, so no trial sees another's baseline, warm-up history
or canary watches.

Scoring (ADR-002 "What counts as correct is the data the destination would have
received with no fault"):

* CORRECT -- every returned row equals its ground truth (strict types), exactly the
  rows with no correct value are quarantined, and ``healed`` is true; for a
  detection-only fault, the rows come back as sent with the required alert and
  ``healed`` false.
* QUARANTINED -- a safe abstention: nothing returned differs from the truth, and the
  rows held aside are unchanged (some or all of the batch), with ``healed`` false or
  with more rows held than needed.
* WRONG -- a returned value differs from the truth, or ``healed`` is true for a
  detection-only fault. A fault the detector never saw comes back unchanged with
  ``healed=true`` and lands here: a missed fault is a failure, not a pass. ``rows``
  go to the sink whatever ``healed`` says (ADR-002 phase 2b: only quarantined rows
  are held back), so a wrong value there is WRONG even when ``healed`` is false.

Row accounting is checked on every trial: returned + quarantined = sent, every
index once, every quarantined row exactly as sent.

What a lane reports (ADR-002 "Precision: applied fixes whose output equals ground
truth"; "Recall: faults healed rather than quarantined") -- ``scores``:

(a) applied-fix precision, GATED (>= 0.999): every fault batch where the healer
    detected something (drift_detected, an alert, or a held row) and returned rows
    counts; it is right when every returned row equals its ground truth. A batch the
    healer flagged and then passed or changed wrongly lands here.
(b) strict outcome accuracy, REPORTED, not gated: CORRECT / (CORRECT + WRONG) over
    every fault batch, a silent miss included (the fault was never detected and the
    data came back as sent, ``WRONG``). Its WRONG trials are split into wrong applied
    fixes (as in (a)), silent misses, and missed alerts (the rows are the truth but a
    detection-only fault went unreported), by fault type and by dataset.
(c) recall: CORRECT / trials.
(d) false alarms on clean batches, GATED at 0.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar

import pytest
from starlette.requests import Request

import metadata_store.db as _metadata_db
from uasr import service
from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.db import get_session_factory, init_uasr_db
from uasr.models import BatchPayload
from uasr.reflector_agent import DiagnosticReflectorAgent

from .datasets import copy_rows, strict_equal
from .faults import Trial

CORRECT, QUARANTINED, WRONG = "CORRECT", "QUARANTINED", "WRONG"

T = TypeVar("T")


async def _no_llm(self, *a, **k):
    return None


def run_isolated(main: Callable[[], Awaitable[T]]) -> Tuple[T, float]:
    """Run a lane on an isolated SQLite file and with no LLM -- the proposal path an
    unproven fault takes would otherwise reach the network. Returns its result and
    the seconds it took."""
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    path = os.path.join(tempfile.gettempdir(), f"aura_heal_bench_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    mp = pytest.MonkeyPatch()
    mp.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    mp.setattr(DiagnosticReflectorAgent, "_llm_diagnosis", _no_llm)
    # Thousands of heals log every recovery step; the lane's table is the output that matters.
    uasr_log = logging.getLogger("uasr")
    log_level = uasr_log.level
    uasr_log.setLevel(logging.CRITICAL)

    async def _go() -> T:
        await init_uasr_db()
        try:
            return await main()
        finally:
            engine = _metadata_db._engine
            if engine is not None:
                await engine.dispose()  # BUG-008: an undisposed aiosqlite engine hangs pytest at exit

    started = time.monotonic()
    try:
        result = asyncio.run(_go())
    finally:
        uasr_log.setLevel(log_level)
        mp.undo()
        _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
        try:
            os.remove(path)
        except OSError:
            pass
    return result, time.monotonic() - started


def report_path(lane: str) -> Optional[str]:
    """Where a lane writes its JSON report: ``AURA_HEAL_BENCH_REPORT`` for the synthetic
    lane, the same name with ``-<lane>`` before the extension for the others."""
    out = os.getenv("AURA_HEAL_BENCH_REPORT")
    if not out or lane == "synthetic":
        return out
    root, ext = os.path.splitext(out)
    return f"{root}-{lane}{ext or '.json'}"


@dataclass(frozen=True)
class DatasetRef:
    name: Optional[str]
    shape: str


@dataclass(frozen=True)
class TrialRef:
    """What a scored trial keeps: enough to group, report and replay it by seed.

    BUG-577 (memory): a Result used to hold the whole Trial (baseline, truth, sent and
    expected rows) and the handler's response (every returned row). On sk_digits (65
    columns) that was ~3.8 MB per trial, and a real lane at 50 trials per (dataset,
    fault) keeps ~10,000 of them -- the 7.4 GB run. The rows are only needed while
    the trial is classified."""
    fault: str
    seed: int
    case_class: str
    detail: str
    dataset: DatasetRef

    @classmethod
    def of(cls, t: Trial) -> "TrialRef":
        return cls(t.fault, t.seed, t.case_class, t.detail, DatasetRef(t.dataset.name, t.dataset.shape))


@dataclass
class Result:
    trial: TrialRef
    outcome: str
    why: str
    accounting_error: Optional[str] = None
    false_alarm: Optional[str] = None
    # (a): the healer detected something (drift, an alert or a held row) and returned
    # ``returned`` rows, ``returned_ok`` of them equal to their ground truth.
    detected: bool = False
    returned: int = 0
    returned_ok: int = 0

    @property
    def applied(self) -> bool:
        """A batch (a) counts: something was detected and rows were returned."""
        return self.detected and self.returned > 0

    @property
    def applied_ok(self) -> bool:
        return self.applied and self.returned_ok == self.returned

    @property
    def wrong_kind(self) -> Optional[str]:
        """Why a WRONG trial is wrong: an applied fix that is wrong ("applied"), a fault
        never detected ("silent miss"), or rows that are the truth with a detection-only
        fault left unreported ("missed alert")."""
        if self.outcome != WRONG:
            return None
        if self.accounting_error or (self.applied and not self.applied_ok):
            return "applied"
        if not self.detected:
            return "silent miss"
        return "missed alert"


def _request(tenant: Optional[str]) -> Request:
    scope = {"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/uasr/heal"}
    if tenant:
        scope["state"] = {"user": {"org_id": tenant, "role": "member"}}
    return Request(scope)


def register(trial: Trial) -> str:
    """A fresh source for ``trial``'s tenant with the dataset's baseline; returns the
    source id as the caller sends it (the handler scopes it to the tenant)."""
    raw = f"bench_{trial.fault}_{trial.seed}_{uuid.uuid4().hex[:8]}"
    scoped = service.scoped_source(trial.tenant, raw)
    ds = trial.dataset
    service._detector.register_baseline(scoped, BatchPayload(
        source_id=scoped, columns=list(ds.schema), rows=ds.baseline, schema_snapshot=ds.schema,
        metadata=dict(trial.baseline_metadata)))
    return raw


async def heal_on(raw: str, trial: Trial) -> Dict[str, Any]:
    """``trial.sent`` through /uasr/heal on an existing source: one batch of a stream."""
    req = service.IngestRequest(
        source_id=raw, rows=copy_rows(trial.sent),
        schema_snapshot=dict(trial.snapshot) if trial.snapshot is not None else None,
        metadata=dict(trial.metadata))
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _request(trial.tenant), db=db)


# The healer's process-wide objects that keep state keyed by source id.
_PER_SOURCE_HOLDERS = ("_detector", "_canary", "_loop", "_tracker", "_gateway", "_matrix")


def forget(raw: str, tenant: Optional[str]) -> None:
    """Drop everything the healer keeps in memory for a source the benchmark is done with.

    Every trial is its own source, and the healer keeps each source's baseline,
    locks and canary state for the life of the process (BUG-578: about 3.7 KB per
    column per source, 240 KB for a 65-column real source). A lane of 10,000 trials
    would otherwise carry 10,000 dead sources -- memory that says nothing about one
    source's accuracy. Production never revisits a benchmark source id, so dropping
    them changes no result; the DB rows (canary watches) stay where they are."""
    scoped = service.scoped_source(tenant, raw)
    store = service._detector._store
    if hasattr(store, "delete"):
        store.delete(scoped)
    for name in _PER_SOURCE_HOLDERS:
        holder = getattr(service, name, None)
        for value in vars(holder).values() if holder is not None else ():
            if isinstance(value, dict):
                value.pop(scoped, None)
            elif isinstance(value, set):
                value.discard(scoped)


async def heal(trial: Trial) -> Dict[str, Any]:
    """A cold start: a new source, its baseline, then the trial's one batch. The
    source is forgotten after it: no later trial may see it."""
    raw = register(trial)
    try:
        return await heal_on(raw, trial)
    finally:
        forget(raw, trial.tenant)


def _accounting(trial: Trial, out: Dict[str, Any]) -> Optional[str]:
    n = len(trial.sent)
    quarantined = out.get("quarantined_rows") or []
    idx = [q.get("index") for q in quarantined]
    if any(not isinstance(i, int) or not 0 <= i < n for i in idx):
        return f"quarantined index out of range: {idx[:5]}"
    if len(set(idx)) != len(idx):
        return "a row is quarantined twice"
    if len(out.get("rows") or []) + len(idx) != n:
        return f"returned {len(out.get('rows') or [])} + quarantined {len(idx)} != sent {n}"
    for q in quarantined:
        if not strict_equal(q.get("row"), trial.sent[q["index"]]):
            return f"quarantined row {q['index']} differs from the row sent"
        if not q.get("reason"):
            return f"quarantined row {q['index']} has no reason"
    return None


def _first_diff(got: Dict[str, Any], want: Optional[Dict[str, Any]]) -> str:
    if want is None:
        return "a row with no correct value (it must be quarantined) was returned"
    keys = sorted(set(got) | set(want))
    for k in keys:
        if k not in got or k not in want or not strict_equal(got[k], want[k]):
            return f"column {k!r}: expected {want.get(k, '<absent>')!r}, got {got.get(k, '<absent>')!r}"
    return "rows differ"


def classify(trial: Trial, out: Dict[str, Any]) -> Result:
    acct = _accounting(trial, out)
    detected = bool(out.get("drift_detected") or out.get("alerts") or out.get("quarantined_rows"))
    if acct:
        return Result(TrialRef.of(trial), WRONG, f"row accounting violated: {acct}", accounting_error=acct,
                      detected=detected)
    held = {q["index"] for q in out["quarantined_rows"]}
    returned = [i for i in range(len(trial.sent)) if i not in held]
    healed = out.get("healed") is True
    alerts = {a.get("type") for a in out.get("alerts") or []}
    must_hold = {i for i, e in enumerate(trial.expected) if e is None}
    ok = [trial.expected[i] is not None and strict_equal(out["rows"][j], trial.expected[i])
          for j, i in enumerate(returned)]

    def result(outcome: str, why: str) -> Result:
        return Result(TrialRef.of(trial), outcome, why, detected=detected,
                      returned=len(returned), returned_ok=sum(ok))

    for j, i in enumerate(returned):
        if not ok[j]:
            return result(WRONG, f"healed={healed}, drift_detected={out.get('drift_detected')}, row {i}: "
                          + _first_diff(out["rows"][j], trial.expected[i]))
    if healed:
        if not trial.expect_healed:
            return result(WRONG, f"healed=true but the detection-only fault was not reported "
                          f"(alerts={sorted(alerts)}, drift_detected={out.get('drift_detected')})")
        if held == must_hold:
            return result(CORRECT, "restored exactly")
        return result(QUARANTINED, f"restored, {len(held - must_hold)} extra row(s) held aside")
    if not trial.expect_healed and trial.required_alerts <= alerts and held == must_hold:
        return result(CORRECT, f"reported {sorted(trial.required_alerts)}, rows untouched")
    return result(QUARANTINED, f"held {len(held)}/{len(trial.sent)} row(s): {_reason(out)}")


def _reason(out: Dict[str, Any]) -> str:
    return str(out.get("reason") or "")[:160]


def classify_clean(trial: Trial, out: Dict[str, Any]) -> Result:
    """Clean data must come back untouched, healed, with no finding of any kind."""
    acct = _accounting(trial, out)
    alarm = None
    if out.get("drift_detected"):
        alarm = f"drift_detected ({out.get('drift_type')}): {_reason(out)}"
    elif out.get("quarantined_rows"):
        alarm = f"{len(out['quarantined_rows'])} row(s) quarantined"
    elif out.get("degraded") or out.get("alerts"):
        alarm = f"degraded, alerts={[a.get('type') for a in out.get('alerts') or []]}"
    elif out.get("healed") is not True:
        alarm = "healed=false"
    elif not strict_equal(out.get("rows"), trial.expected):
        alarm = "rows changed"
    outcome = CORRECT if not (alarm or acct) else WRONG
    return Result(TrialRef.of(trial), outcome, alarm or acct or "clean", accounting_error=acct, false_alarm=alarm)


# ── Metrics ────────────────────────────────────────────────────────────────────

@dataclass
class Metrics:
    trials: int
    correct: int
    quarantined: int
    wrong: int

    @property
    def precision(self) -> float:
        # No applied fix and no wrong value is a precision of 1: abstaining is never wrong.
        decided = self.correct + self.wrong
        return self.correct / decided if decided else 1.0

    @property
    def recall(self) -> float:
        return self.correct / self.trials if self.trials else 0.0


def metrics(results: List[Result]) -> Metrics:
    c = Counter(r.outcome for r in results)
    return Metrics(len(results), c[CORRECT], c[QUARANTINED], c[WRONG])


def by_fault(results: List[Result]) -> Dict[str, Metrics]:
    groups: Dict[str, List[Result]] = defaultdict(list)
    for r in results:
        groups[r.trial.fault].append(r)
    return {k: metrics(v) for k, v in groups.items()}


@dataclass
class Applied:
    """(a) applied-fix precision: fault batches where something was detected and rows
    were returned, and how many of them returned only ground truth."""
    batches: int
    correct: int
    rows: int
    rows_correct: int

    @property
    def wrong(self) -> int:
        return self.batches - self.correct

    @property
    def precision(self) -> float:
        return self.correct / self.batches if self.batches else 1.0

    @property
    def row_precision(self) -> float:
        return self.rows_correct / self.rows if self.rows else 1.0

    def to_dict(self) -> Dict[str, Any]:
        return vars(self) | {"wrong": self.wrong, "precision": self.precision,
                             "row_precision": self.row_precision}


def applied(results: List[Result]) -> Applied:
    mine = [r for r in results if r.applied]
    return Applied(len(mine), sum(1 for r in mine if r.applied_ok),
                   sum(r.returned for r in mine), sum(r.returned_ok for r in mine))


WRONG_KINDS = ("applied", "silent miss", "missed alert")


def _name(r: Result) -> str:
    return r.trial.dataset.name or r.trial.dataset.shape


def _strict(results: List[Result]) -> Dict[str, Any]:
    m = metrics(results)
    kinds = Counter(r.wrong_kind for r in results if r.outcome == WRONG)
    return {"trials": m.trials, "correct": m.correct, "quarantined": m.quarantined, "wrong": m.wrong,
            "accuracy": m.precision, "recall": m.recall, "wrong_by_kind": {k: kinds[k] for k in WRONG_KINDS}}


def _groups(results: List[Result], key: Callable[[Result], str]) -> Dict[str, List[Result]]:
    out: Dict[str, List[Result]] = defaultdict(list)
    for r in results:
        out[key(r)].append(r)
    return dict(sorted(out.items()))


def scores(results: List[Result], clean: List[Result]) -> Dict[str, Any]:
    """The lane's (a)-(d), as the module docstring defines them."""
    faults = _groups(results, lambda r: r.trial.fault)
    return {
        "a_applied_fix_precision": applied(results).to_dict()
        | {"gate": 0.999, "per_fault": {f: applied(rs).to_dict() for f, rs in faults.items()}},
        "b_strict_outcome_accuracy": _strict(results) | {
            "gated": False,
            "per_fault": {f: _strict(rs) for f, rs in faults.items()},
            "per_dataset": {d: _strict(rs) for d, rs in _groups(results, _name).items()}},
        "c_recall": metrics(results).recall,
        "d_clean_false_alarms": {"trials": len(clean), "false_alarms": sum(1 for r in clean if r.false_alarm),
                                 "gate": 0},
    }


def table(results: List[Result], clean: List[Result]) -> str:
    a, m = applied(results), metrics(results)
    alarms = sum(1 for r in clean if r.false_alarm)
    kinds = Counter(r.wrong_kind for r in results if r.outcome == WRONG)
    lines = [
        f"(a) applied-fix precision, GATED >= 0.999: {a.precision:.4f} -- {a.correct} of {a.batches} batches "
        f"where a fault was detected and rows returned gave only ground truth "
        f"(rows: {a.rows_correct}/{a.rows} = {a.row_precision:.5f})",
        f"(b) strict outcome accuracy, silent misses included, REPORTED not gated: {m.precision:.4f} -- "
        f"{m.wrong} WRONG of {m.trials}: " + ", ".join(f"{kinds[k]} {k}" for k in WRONG_KINDS),
        f"(c) recall: {m.recall:.4f} ({m.correct} healed or correctly reported of {m.trials})",
        f"(d) clean false alarms, GATED at 0: {alarms} of {len(clean)}",
        f"{'fault':<18}{'trials':>7}{'correct':>9}{'quar.':>7}{'wrong':>7}{'(a)prec':>9}{'(a)n':>6}"
        f"{'(b)acc':>8}{'silent':>8}{'alert':>7}{'recall':>8}",
    ]

    def line(label: str, rs: List[Result]) -> str:
        fm, fa = metrics(rs), applied(rs)
        k = Counter(r.wrong_kind for r in rs if r.outcome == WRONG)
        return (f"{label:<18}{fm.trials:>7}{fm.correct:>9}{fm.quarantined:>7}{fm.wrong:>7}"
                f"{fa.precision:>9.4f}{fa.batches:>6}{fm.precision:>8.4f}{k['silent miss']:>8}"
                f"{k['missed alert']:>7}{fm.recall:>8.3f}")

    lines += [line(f, rs) for f, rs in _groups(results, lambda r: r.trial.fault).items()]
    lines.append(line("OVERALL", results))
    lines.append("by dataset ((a) precision, (b) strict accuracy, recall, clean false alarms):")
    by_clean = _groups(clean, _name)
    for name, rs in _groups(results, _name).items():
        dm, da, c = metrics(rs), applied(rs), by_clean.get(name, [])
        lines.append(f"  {name:<26} (a) {da.precision:.4f} of {da.batches}  (b) {dm.precision:.4f}  "
                     f"recall {dm.recall:.3f}  false alarms {sum(1 for r in c if r.false_alarm)}/{len(c)}")
    return "\n".join(lines)


def report(results: List[Result], clean: List[Result]) -> Dict[str, Any]:
    """The run as JSON: (a)-(d), metrics per fault type and every non-CORRECT trial,
    replayable by seed. ``overall``/``per_fault`` "precision" is (b), the strict outcome
    accuracy, under its old key so earlier reports compare."""
    def row(r: Result) -> Dict[str, Any]:
        return {"fault": r.trial.fault, "seed": r.trial.seed, "outcome": r.outcome, "wrong_kind": r.wrong_kind,
                "dataset": _name(r), "case_class": r.trial.case_class, "detail": r.trial.detail, "why": r.why}
    per = {f: vars(m) | {"precision": m.precision, "recall": m.recall} for f, m in by_fault(results).items()}
    m = metrics(results)
    return {
        "scores": scores(results, clean),
        "applied_wrong": [row(r) for r in results if r.applied and not r.applied_ok],
        "overall": vars(m) | {"precision": m.precision, "recall": m.recall},
        "per_fault": per,
        "clean": {"trials": len(clean), "false_alarms": sum(1 for r in clean if r.false_alarm)},
        "not_correct": [row(r) for r in results + clean if r.outcome != CORRECT],
    }


def failing_applied(results: List[Result], limit: int = 40) -> str:
    """Each case class that fails (a), once, with its count and a seed to replay."""
    return failing_classes([r for r in results if r.applied and not r.applied_ok], limit)


def failing_classes(results: List[Result], limit: int = 40) -> str:
    """Each WRONG (or false-alarm) case class once, with its count and a seed to replay."""
    groups: Dict[str, List[Result]] = defaultdict(list)
    for r in results:
        if r.outcome == WRONG:
            kind = f" [{r.wrong_kind}]" if r.wrong_kind else ""
            groups[f"{r.trial.fault}{kind} | {r.trial.case_class}"].append(r)
    lines = []
    for key, rs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit]:
        ex = rs[0]
        lines.append(f"{len(rs):>4} x {key} | e.g. seed={ex.trial.seed}: {ex.trial.detail} -> {ex.why}")
    return "\n".join(lines)
