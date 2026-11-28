"""Multi-batch steady state for the ADR-002 benchmark (BUG-577).

Every synthetic and real trial is a cold start: one baseline, one batch. A source in
production sees hundreds of batches, and the healer carries state across them -- the
detector's KL window and warm-up, the canary's watches, kept repairs and rollbacks.
A stream here is one source run the way a pipeline runs it:

1. a baseline, then 5-10 clean batches (warm-up and beyond: no false alarm allowed);
2. a fault at the next batch, scored exactly as a cold-start trial is;
3. when that batch deployed a repair (the canary opened a watch), one of:
   * keep -- the fault persists for the canary's N batches; every batch must be
     restored and the repair kept;
   * rollback (rescales) -- the next batch carries the fault plus an unprovable x7
     on the same column: it must be quarantined and the canary must roll the repair
     back; the plain fault after it must be quarantined (the repair is blocked);
     then time is moved past the block (the rollback's rows are aged in the DB and
     the source's canary state reloaded) and the plain fault must be repaired again
     and watched afresh;
4. 2-4 clean batches after it, again with no false alarm.

Keep, precisely (steady seeds 194 and 336): no batch of the fault rolls the repair
back, and after N batches the repair is kept or still watched -- a batch that could
not prove it again is quarantined and is no evidence against it (ADR-002: never
apply a repair blindly; a rollback of a correct mapping blocks it on every later
batch). Such a batch costs recall, which the miss floor holds.

About half the streams declare a unique key (the synthetic identifier, a real
source's natural key, or its row position as ``row_key``): their clean batches are
sometimes two pages of one run (``metadata.run_id``), which must not alarm, and a
duplicate on them -- keyed duplicates, or plain duplicates -- must get the alert
every time (``Trial.keyed_dup``, gated at 0 misses).

Fault batches go through ``classify`` and clean ones through ``classify_clean``, so
precision, false alarms and row accounting mean what they mean in the other lanes.
A stream is rebuilt from its seed alone.
"""
from __future__ import annotations

import functools
import random
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import update

from uasr import service
from uasr.db import get_session_factory
from uasr.models import RepairWatch
from uasr.repair_canary import ROLLED_BACK, load_watches, repair_signature

from .datasets import Dataset, copy_rows, make_dataset, sample, strict_equal
from .faults import COMBOS, FAULT_TYPES, INJECTORS, NATURAL_KEYS, ROW_KEY, NotApplicable, Trial
from .harness import CORRECT, QUARANTINED, Result, classify, classify_clean, forget, heal_pages_on, register
from .realdata import REAL_SOURCES, load, make_real_dataset, sample_rows


@functools.lru_cache(maxsize=None)
def steady_sources() -> Tuple[str, ...]:
    """Real sources with rows enough for a baseline and a stream of distinct batches.
    Loaded on first use, not at import: collecting the suite must not need sklearn."""
    return tuple(s for s in REAL_SOURCES if len(load(s)[1]) >= 500)


SCALE_FAULTS = ("scale_up", "scale_down")
# A rollback blocks a repair for an hour first (repair_canary.BLOCK_BACKOFF).
PAST_FIRST_BLOCK = timedelta(hours=1, minutes=30)


@dataclass
class Scenario:
    seed: int
    kind: str  # keep | rollback
    fault: str
    dataset: str
    ok: bool
    why: str


@dataclass
class StreamResult:
    faults: List[Result]
    clean: List[Result]
    scenarios: List[Scenario]


def _signatures(out: Dict[str, Any], key: str) -> Set[Tuple[str, str, str]]:
    return {(w["type"], w["column"], w["param"]) for w in (out.get("canary") or {}).get(key, [])}


class Stream:
    """One source: its dataset, tenant, snapshot habit and the batches it sends."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rnd = random.Random(f"steady:{seed}")
        rnd = self.rnd
        # Even seeds stream a synthetic dataset, odd ones a real source in turn.
        real = steady_sources()
        self.source: Optional[str] = None if seed % 2 == 0 else real[(seed // 2) % len(real)]
        self.fault = FAULT_TYPES[(seed // 2) % len(FAULT_TYPES)]
        # Keyed or not, and how its clean batches page, from a stream of its own, so the
        # rows and faults of every stream are drawn as they were before keys existed.
        self.krnd = random.Random(f"steady-keyed:{seed}")
        keyed = self.fault == "keyed_duplicates" or self.krnd.random() < 0.5
        row_key = ROW_KEY if keyed and self.source is not None and self.source not in NATURAL_KEYS else None
        self.row_key = row_key
        if self.source is None:
            self.ds: Dataset = make_dataset(rnd.randrange(1 << 30))
            self._ordinal = len(self.ds.baseline) + len(self.ds.truth)
        else:
            self.ds = make_real_dataset(self.source, rnd.randrange(1 << 30), row_key=row_key)
        self.key: List[str] = []
        if keyed:
            self.key = (list(NATURAL_KEYS[self.source]) if self.source in NATURAL_KEYS
                        else [row_key or self.ds.columns[0].name])
        self.snapshot = rnd.random() < 0.5
        self.tenant = rnd.choice([None, "tenant_a", "tenant_b"])
        # A volume fault is judged only between complete runs.
        self.complete_run = self.fault == "volume_drop" or rnd.random() < 0.3
        self.raw: Optional[str] = None

    @property
    def name(self) -> str:
        return self.ds.shape

    def _rows(self) -> List[Dict[str, Any]]:
        n = self.rnd.randint(150, 600) if self.source is None else self.rnd.randint(150, 400)
        if self.source is None:
            rows = sample(self.ds.columns, n, self.rnd, self._ordinal)
            self._ordinal += n
            return rows
        return sample_rows(self.source, self.rnd, n, self.ds.baseline_positions, row_key=self.row_key)

    def _trial(self, label: str, truth: List[Dict[str, Any]]) -> Trial:
        meta = {"complete_run": True} if self.complete_run else {}
        base = dict(meta) | ({"key_columns": list(self.key)} if self.key else {})
        return Trial(
            fault=label, seed=self.seed, dataset=replace(self.ds, truth=truth),
            sent=copy_rows(truth), expected=copy_rows(truth),
            snapshot=dict(self.ds.schema) if self.snapshot else None, tenant=self.tenant,
            metadata=dict(meta), baseline_metadata=base)

    def clean(self, label: str = "clean") -> Trial:
        t = self._trial(label, self._rows())
        t.note(label, label)
        if self.key and self.krnd.random() < 0.5:
            # Two pages of one run: no key repeats across them, so neither may alarm.
            half = len(t.sent) // 2
            t.prior_pages = [t.sent[:half]]
            t.sent, t.expected = t.sent[half:], t.expected[half:]
            t.metadata["run_id"] = f"run-{self.seed}-{self.krnd.randrange(1 << 30)}"
            if t.metadata.get("complete_run"):
                t.metadata["run_rows"] = half + len(t.sent)
        return t

    def faulted(self, label: str, fault: str, fault_seed: str) -> Trial:
        """``fault`` on a fresh batch. The injector's choices (column, factor, new name)
        come from ``fault_seed`` before any row is read, so the same seed repeats the
        same upstream fault on every batch it is used for."""
        for _ in range(20):
            t = self._trial(label, self._rows())
            frnd = random.Random(fault_seed)
            steps = list(frnd.choice(COMBOS)) if fault == "combo" else [fault]
            for step in steps:
                INJECTORS[step](t, frnd)  # NotApplicable: the caller picks another fault
            if not strict_equal(t.sent, t.dataset.truth):
                return t
        raise NotApplicable(fault)

    def regressing(self, fault_seed: str) -> Trial:
        """The rescale fault plus an x7 on the same column: no factor restores it."""
        t = self.faulted("regress", self.fault, fault_seed)
        (col,) = t.used
        original = [r.get(col) for r in t.dataset.truth]
        t.map_column(col, lambda v: v * 7)
        t.void_changed(col, original)
        t.note(f"x7 on top, on {col}", "x7 on top of the rescale")
        return t


async def _age_rollbacks(scoped: str, by: timedelta) -> None:
    """Move the source's rollbacks ``by`` into the past and reload its canary state:
    the block then reads as having started that long ago."""
    async with get_session_factory()() as db:
        for row in await load_watches(scoped):
            if row.status == ROLLED_BACK:
                await db.execute(update(RepairWatch).where(RepairWatch.id == row.id).values(
                    decided_at=row.decided_at - by, created_at=row.created_at - by))
        await db.commit()
    service._canary.hydrate(await load_watches(scoped), scoped)


async def run_stream(seed: int) -> StreamResult:
    s = Stream(seed)
    rnd = s.rnd
    faults: List[Result] = []
    clean: List[Result] = []
    scenarios: List[Scenario] = []
    first = s._trial("baseline", [])
    s.raw = register(first)
    scoped = service.scoped_source(s.tenant, s.raw)

    async def send(t: Trial, is_clean: bool = False) -> Tuple[Result, Dict[str, Any]]:
        if s.key and "duplicate" in t.required_alerts:
            t.keyed_dup = True  # a duplicate on a source with a declared key: owed the alert
        out, pages = await heal_pages_on(s.raw, t)
        clean.extend(pages)
        r = classify_clean(t, out) if is_clean else classify(t, out)
        (clean if is_clean else faults).append(r)
        return r, out

    for _ in range(rnd.randint(5, 10)):
        await send(s.clean("clean before"), True)

    # The fault this stream carries, or the next type that applies to its dataset.
    fault_seed = f"steady-fault:{seed}"
    trial = None
    for k in range(len(FAULT_TYPES)):
        fault = FAULT_TYPES[(FAULT_TYPES.index(s.fault) + k) % len(FAULT_TYPES)]
        if fault == "keyed_duplicates" and not s.key:
            continue  # its key must be declared with the baseline
        try:
            trial = s.faulted(fault, fault, fault_seed)
            s.fault = fault
            break
        except NotApplicable:
            continue
    assert trial is not None, f"no fault applies to stream {seed}"
    r, out = await send(trial)
    opened = _signatures(out, "opened")
    rescaled = {sig for sig in opened if sig[0] == "rescale"}

    if opened and r.outcome == CORRECT:
        if s.fault in SCALE_FAULTS and rescaled and rnd.random() < 0.5:
            scenarios.append(await _rollback(s, send, fault_seed, rescaled, scoped))
        else:
            scenarios.append(await _keep(s, send, fault_seed, opened, scoped))

    for _ in range(rnd.randint(2, 4)):
        await send(s.clean("clean after"), True)
    forget(s.raw, s.tenant)  # the stream is over; see harness.forget
    return StreamResult(faults, clean, scenarios)


async def _keep(s: Stream, send, fault_seed: str, opened: Set[Tuple[str, str, str]], scoped: str) -> Scenario:
    """The fault persists for the canary's N batches. No batch may roll a repair back,
    and after them every repair is kept, or still watched because some batch could
    not prove it again (that batch is quarantined: recall, not accuracy)."""
    kept: Set[Tuple[str, str, str]] = set()
    why, unproven = [], []
    for i in range(service._canary.batches_required):
        r, out = await send(s.faulted("canary repeat", s.fault, fault_seed))
        kept |= _signatures(out, "kept")
        if _signatures(out, "rolled_back"):
            why.append(f"batch {i + 1} rolled back {sorted(_signatures(out, 'rolled_back'))}: {r.why}")
        elif r.outcome == QUARANTINED:
            unproven.append(i + 1)
    watched = {w.signature for w in service._canary.watches(scoped)}
    lost = opened - kept - watched
    if lost:
        why.append(f"neither kept nor watched after {service._canary.batches_required} batches: {sorted(lost)}")
    if why:
        return Scenario(s.seed, "keep", s.fault, s.name, False, "; ".join(why))
    if opened - kept:
        return Scenario(s.seed, "keep", s.fault, s.name, True,
                        f"still watched: {sorted(opened - kept)} (batch(es) {unproven} could not prove it "
                        "again and were quarantined)")
    return Scenario(s.seed, "keep", s.fault, s.name, True, "kept")


async def _rollback(s: Stream, send, fault_seed: str, sigs: Set[Tuple[str, str, str]], scoped: str) -> Scenario:
    why = []
    r, out = await send(s.regressing(fault_seed))
    if not sigs <= _signatures(out, "rolled_back"):
        why.append(f"regressing batch did not roll back {sorted(sigs)} ({r.outcome}: {r.why})")
    blocked = service._canary.blocked(scoped)
    if not sigs <= blocked:
        why.append(f"not blocked after the rollback: {sorted(blocked)}")
    r, out = await send(s.faulted("blocked", s.fault, fault_seed))
    applied = {repair_signature(x["type"], x["column"], x["proof"]) for x in out.get("repairs") or []}
    if r.outcome != QUARANTINED or applied & sigs:
        why.append(f"blocked repair applied or not held ({r.outcome}: {r.why})")
    await _age_rollbacks(scoped, PAST_FIRST_BLOCK)
    if service._canary.blocked(scoped) & sigs:
        why.append("still blocked after the block expired")
    r, out = await send(s.faulted("re-proven", s.fault, fault_seed))
    if r.outcome != CORRECT or not sigs <= _signatures(out, "opened"):
        why.append(f"not re-proven and watched after expiry ({r.outcome}: {r.why}; "
                   f"opened {sorted(_signatures(out, 'opened'))})")
    return Scenario(s.seed, "rollback", s.fault, s.name, not why, "; ".join(why) or "rolled back, blocked, re-proven")
