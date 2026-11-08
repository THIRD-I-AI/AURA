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
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from starlette.requests import Request

from uasr import service
from uasr.db import get_session_factory
from uasr.models import BatchPayload

from .datasets import copy_rows, strict_equal
from .faults import Trial

CORRECT, QUARANTINED, WRONG = "CORRECT", "QUARANTINED", "WRONG"


@dataclass
class Result:
    trial: Trial
    outcome: str
    why: str
    accounting_error: Optional[str] = None
    false_alarm: Optional[str] = None
    response: Dict[str, Any] = field(default_factory=dict)


def _request(tenant: Optional[str]) -> Request:
    scope = {"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/uasr/heal"}
    if tenant:
        scope["state"] = {"user": {"org_id": tenant, "role": "member"}}
    return Request(scope)


async def heal(trial: Trial) -> Dict[str, Any]:
    raw = f"bench_{trial.fault}_{trial.seed}_{uuid.uuid4().hex[:8]}"
    scoped = service.scoped_source(trial.tenant, raw)
    ds = trial.dataset
    service._detector.register_baseline(scoped, BatchPayload(
        source_id=scoped, columns=list(ds.schema), rows=ds.baseline, schema_snapshot=ds.schema,
        metadata=dict(trial.baseline_metadata)))
    req = service.IngestRequest(
        source_id=raw, rows=copy_rows(trial.sent),
        schema_snapshot=dict(trial.snapshot) if trial.snapshot is not None else None,
        metadata=dict(trial.metadata))
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _request(trial.tenant), db=db)


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
    if acct:
        return Result(trial, WRONG, f"row accounting violated: {acct}", accounting_error=acct, response=out)
    held = {q["index"] for q in out["quarantined_rows"]}
    returned = [i for i in range(len(trial.sent)) if i not in held]
    healed = out.get("healed") is True
    alerts = {a.get("type") for a in out.get("alerts") or []}
    must_hold = {i for i, e in enumerate(trial.expected) if e is None}
    for j, i in enumerate(returned):
        if trial.expected[i] is None or not strict_equal(out["rows"][j], trial.expected[i]):
            return Result(trial, WRONG,
                          f"healed={healed}, drift_detected={out.get('drift_detected')}, row {i}: "
                          + _first_diff(out["rows"][j], trial.expected[i]), response=out)
    if healed:
        if not trial.expect_healed:
            return Result(trial, WRONG, f"healed=true but the detection-only fault was not reported "
                          f"(alerts={sorted(alerts)}, drift_detected={out.get('drift_detected')})", response=out)
        if held == must_hold:
            return Result(trial, CORRECT, "restored exactly", response=out)
        return Result(trial, QUARANTINED, f"restored, {len(held - must_hold)} extra row(s) held aside", response=out)
    if not trial.expect_healed and trial.required_alerts <= alerts and held == must_hold:
        return Result(trial, CORRECT, f"reported {sorted(trial.required_alerts)}, rows untouched", response=out)
    return Result(trial, QUARANTINED, f"held {len(held)}/{len(trial.sent)} row(s): {_reason(out)}", response=out)


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
    return Result(trial, outcome, alarm or acct or "clean", accounting_error=acct, false_alarm=alarm, response=out)


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


def table(results: List[Result], clean: List[Result]) -> str:
    lines = [f"{'fault':<18}{'trials':>7}{'correct':>9}{'quar.':>7}{'wrong':>7}{'precision':>11}{'recall':>8}"]
    for fault, m in sorted(by_fault(results).items()):
        lines.append(f"{fault:<18}{m.trials:>7}{m.correct:>9}{m.quarantined:>7}{m.wrong:>7}"
                     f"{m.precision:>11.4f}{m.recall:>8.3f}")
    m = metrics(results)
    lines.append(f"{'OVERALL':<18}{m.trials:>7}{m.correct:>9}{m.quarantined:>7}{m.wrong:>7}"
                 f"{m.precision:>11.4f}{m.recall:>8.3f}")
    alarms = sum(1 for r in clean if r.false_alarm)
    lines.append(f"clean batches: {len(clean)}, false alarms: {alarms} "
                 f"(rate {alarms / len(clean) if clean else 0:.4f})")
    lines.append("by dataset shape (fault precision / recall, clean false alarms):")
    shapes = sorted({r.trial.dataset.shape for r in results + clean})
    for shape in shapes:
        m = metrics([r for r in results if r.trial.dataset.shape == shape])
        c = [r for r in clean if r.trial.dataset.shape == shape]
        lines.append(f"  {shape:<26} precision {m.precision:.4f}  recall {m.recall:.3f}  "
                     f"false alarms {sum(1 for r in c if r.false_alarm)}/{len(c)}")
    return "\n".join(lines)


def report(results: List[Result], clean: List[Result]) -> Dict[str, Any]:
    """The run as JSON: metrics per fault type and every non-CORRECT trial, replayable by seed."""
    def row(r: Result) -> Dict[str, Any]:
        return {"fault": r.trial.fault, "seed": r.trial.seed, "outcome": r.outcome,
                "case_class": r.trial.case_class, "detail": r.trial.detail, "why": r.why}
    per = {f: vars(m) | {"precision": m.precision, "recall": m.recall} for f, m in by_fault(results).items()}
    m = metrics(results)
    return {
        "overall": vars(m) | {"precision": m.precision, "recall": m.recall},
        "per_fault": per,
        "clean": {"trials": len(clean), "false_alarms": sum(1 for r in clean if r.false_alarm)},
        "not_correct": [row(r) for r in results + clean if r.outcome != CORRECT],
    }


def failing_classes(results: List[Result], limit: int = 40) -> str:
    """Each WRONG (or false-alarm) case class once, with its count and a seed to replay."""
    groups: Dict[str, List[Result]] = defaultdict(list)
    for r in results:
        if r.outcome == WRONG:
            groups[f"{r.trial.fault} | {r.trial.case_class}"].append(r)
    lines = []
    for key, rs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit]:
        ex = rs[0]
        lines.append(f"{len(rs):>4} x {key} | e.g. seed={ex.trial.seed}: {ex.trial.detail} -> {ex.why}")
    return "\n".join(lines)
