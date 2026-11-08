"""The post-heal canary for proven repairs (ADR-002 safety net step 2, BUG-565).

What it watches. Since phase 2a item 1 a repair is not a standing shim: /uasr/heal
proves it again on every batch (``RecoveryLoop.prove_repairs``) and files the batch's
RecoveryRecord as ``deployed`` with ``generation_method='proven'``. So the thing that
can turn out wrong is the *repair itself* -- "rename ``amt`` back to ``amount``",
"cast ``id`` from str back to int", "divide ``amount`` by 100" -- whose proof on one
batch may have been a coincidence. Each repair a source first gets opens one watch,
keyed by its signature (type, column, what pins it down), and every later batch of
the source is evidence for or against it:

* clean -- the batch proved the same repair again, or the fault it fixes is gone;
* regressed -- the batch drifted on the repair's own column and no proven repair
  brings it back: its rename is contradicted, its type change re-fires with no proven
  cast back, the column is still off-baseline after the repairs;
* inconclusive -- an earlier fault the batch could not prove stopped the proof before
  the repair's stage, or a different proven repair of the same kind restored the
  column (upstream switched from cents to mills: the x1000 rescale fixed the batch,
  so the x100 was neither wrong nor exercised), so the batch says nothing about it.

N clean batches (``UASR_POST_HEAL_VALIDATION_BATCHES``, default 3) keep the repair;
one regression rolls it back: the repair is blocked for the source, so a batch it
would apply to is quarantined instead, and its rows never reach a caller again. Drift
on other columns is not the repair's regression and never rolls it back; the proof
handles it on its own (quarantine, alert).

One watch per repair, not per source: a second repair for the source opens its own
watch and leaves the first one's running. The watch, the rollback and the kept repair
live in ``uasr_repair_watches``. A rollback is written there before the batch that
caused it, or any later batch of the source, is answered: if the write fails the
source fails closed (``RollbackNotPersisted``, a 503) until it succeeds, so no caller
is answered on a decision the DB does not hold, and a restart or another replica
cannot bring a rolled-back repair back. Only an operator lifts a rollback (``clear``):
its row becomes ``cleared`` and the repair may be proven and watched afresh.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from sqlalchemy import select, update

from .db import get_session_factory
from .models import RecoveryStatus, RepairWatch
from .tenancy import owns_source

logger = logging.getLogger("uasr.repair_canary")

Signature = Tuple[str, str, str]

CLEAN, REGRESSED, INCONCLUSIVE = "clean", "regressed", "inconclusive"
WATCHING = "watching"
KEPT = RecoveryStatus.DEPLOYED.value
ROLLED_BACK = RecoveryStatus.ROLLED_BACK.value
# An operator lifted the rollback; the row stays as the audit trail and blocks nothing.
CLEARED = "cleared"

# A rollback blocks its repair for a while, not for good: one false rollback must not
# leave a source needing a person (ADR-002: no human gate). Repeated rollbacks of the
# same repair within BACKOFF_WINDOW block it longer. Expiry applies nothing by itself:
# the repair has to be proven again on a fresh batch and passes the canary again.
BLOCK_BACKOFF = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
BACKOFF_WINDOW = timedelta(days=7)


def _utc(ts: Optional[datetime]) -> Optional[datetime]:
    # NaiveUTCDateTime columns load naive; compare everything as aware UTC.
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class RollbackNotPersisted(RuntimeError):
    """A rollback is in force in memory but its ROLLED_BACK row is not written yet;
    the source must not be answered until it is."""


def repair_signature(repair_type: str, column: str, proof: Dict[str, Any]) -> Optional[Signature]:
    """What identifies a repair across batches; None for one that changes no value.

    A kept new column changes nothing in any row (ADR-002 "New column"), so there is
    nothing a canary could roll back."""
    if repair_type == "rename":
        return ("rename", column, str(proof.get("from")))
    if repair_type == "retype":
        return ("retype", column, f"{proof.get('from')}->{proof.get('to')}")
    if repair_type == "rescale":
        return ("rescale", column, repr(float(proof["factor"])))
    return None


def judge(sig: Signature, evidence: Optional[Dict[str, Any]]) -> str:
    """One batch's verdict on one watched repair. ``evidence`` is the batch's
    ``ProvenHealPlan.canary_evidence``, or None for a batch with no drift at all."""
    if evidence is None:
        return CLEAN
    kind, column, param = sig
    if column in evidence["failed_columns"]:
        return REGRESSED
    if any(r[0] == kind and r[1] == column for r in evidence.get("refused", ())):
        # The batch carried the fault of a rolled-back repair of this kind on this
        # column; it was quarantined and says nothing about this watch.
        return INCONCLUSIVE
    if sig in evidence["proven"]:
        return CLEAN
    if kind == "rename":
        # The rename stage runs on every schema change, so this is always decidable:
        # the old name is back (fault gone), or the new one is here and was not proven.
        cols = evidence["columns"]
        return REGRESSED if param in cols and column not in cols else CLEAN
    seen = evidence["retype_seen"] if kind == "retype" else evidence["scale_seen"]
    if seen is None:
        return INCONCLUSIVE
    if column not in seen:
        return CLEAN
    # The column drifted again in this repair's stage but is not in failed_columns, so
    # the sandbox passed on it. If a proven repair of the same kind (another factor, a
    # cast from another type) is what restored it, upstream changed the fault and the
    # watched repair was not applied: it neither failed nor held (review of bb2bbac:
    # blaming it rolled back correct repairs until none was left).
    if any(p[0] == kind and p[1] == column for p in evidence["proven"]):
        return INCONCLUSIVE
    return REGRESSED


@dataclass
class Watch:
    source_id: str
    recovery_id: str
    signature: Signature
    batches_required: int
    proof: Dict[str, Any] = field(default_factory=dict)
    batches_seen: int = 0
    status: str = WATCHING
    reason: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    decided_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        kind, column, param = self.signature
        return {
            "watch_id": self.id, "source_id": self.source_id, "recovery_id": self.recovery_id,
            "type": kind, "column": column, "param": param, "status": self.status,
            "batches_seen": self.batches_seen, "batches_required": self.batches_required,
            "reason": self.reason,
        }


@dataclass
class Outcome:
    """What one batch did to a source's watches."""
    rolled_back: List[Watch] = field(default_factory=list)
    kept: List[Watch] = field(default_factory=list)
    advanced: List[Watch] = field(default_factory=list)
    opened: List[Watch] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rolled_back": [w.to_dict() for w in self.rolled_back],
            "kept": [w.to_dict() for w in self.kept],
            "opened": [w.to_dict() for w in self.opened],
        }


class RepairCanary:
    """In-memory canary state; ``observe`` is the only thing that changes it per batch.

    Callers hold ``lock(source_id)`` from reading a source's watches to acting on them,
    so two concurrent heals for one source are judged one after the other, each on
    the state the previous one left (review of 9180b5b: a watch read before an await
    and acted on after it could already be stale)."""

    def __init__(self, batches_required: int) -> None:
        self.batches_required = batches_required
        self._watches: Dict[str, Dict[Signature, Watch]] = {}
        self._kept: Dict[str, Set[Signature]] = {}
        self._rolled_back: Dict[str, Dict[Signature, Watch]] = {}
        # When each repair was rolled back for each source (cleared ones excluded).
        self._rollback_times: Dict[str, Dict[Signature, List[datetime]]] = {}
        # Rollbacks in force in memory whose ROLLED_BACK row could not be written yet.
        self._unpersisted: Dict[str, List[Watch]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        # False until the durable state of every source has been loaded once. Until
        # then no rollback from before this start is known, so ``sync`` fails closed
        # (review of 2ce9d08: a failed startup load used to leave the canary empty
        # for the life of the process).
        self.hydrated = False
        self._hydrate_lock = asyncio.Lock()
        # Sources whose memory a lost compare-and-set showed to be behind the DB and
        # that could not be reloaded at once; ``sync`` reloads them first.
        self._stale: Set[str] = set()

    def lock(self, source_id: str) -> asyncio.Lock:
        return self._locks.setdefault(source_id, asyncio.Lock())

    def block_until(self, source_id: str, sig: Signature) -> Optional[datetime]:
        """When the rollback's block on ``sig`` ends, or None if there is no rollback."""
        w = self._rolled_back.get(source_id, {}).get(sig)
        if w is None:
            return None
        decided = _utc(w.decided_at)
        if decided is None:  # a pending rollback with no time: keep it blocked
            return datetime.max.replace(tzinfo=timezone.utc)
        times = self._rollback_times.get(source_id, {}).get(sig) or [decided]
        recent = sum(1 for t in times if decided - BACKOFF_WINDOW < t <= decided)
        return decided + BLOCK_BACKOFF[min(max(recent, 1), len(BLOCK_BACKOFF)) - 1]

    def blocked(self, source_id: str, now: Optional[datetime] = None) -> FrozenSet[Signature]:
        now = now or datetime.now(timezone.utc)
        return frozenset(sig for sig in self._rolled_back.get(source_id, {})
                         if now < self.block_until(source_id, sig))

    def _note_rollback(self, w: Watch) -> None:
        self._rolled_back.setdefault(w.source_id, {})[w.signature] = w
        decided = _utc(w.decided_at)
        times = self._rollback_times.setdefault(w.source_id, {}).setdefault(w.signature, [])
        if decided is not None and decided not in times:
            times.append(decided)

    def watches(self, source_id: str) -> List[Watch]:
        return list(self._watches.get(source_id, {}).values())

    def visible(self, tenant: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
        """The canary state of ``tenant``'s sources only (BUG-263)."""
        def own(by_source: Dict[str, Dict[Signature, Watch]]) -> List[Dict[str, Any]]:
            return [w.to_dict() for sid, ws in by_source.items() if owns_source(tenant, sid)
                    for w in ws.values()]
        now = datetime.now(timezone.utc)
        rolled = [{**w.to_dict(), "blocked_until": self.block_until(sid, sig).isoformat(),
                   "blocked": now < self.block_until(sid, sig)}
                  for sid, ws in self._rolled_back.items() if owns_source(tenant, sid)
                  for sig, w in ws.items()]
        return {
            "watching": own(self._watches),
            "rolled_back": rolled,
            "unpersisted_rollbacks": sum(
                len(ws) for sid, ws in self._unpersisted.items() if owns_source(tenant, sid)),
        }

    async def ensure_hydrated(self) -> None:
        """Load every source's durable state once; raises while the DB cannot be read."""
        if self.hydrated:
            return
        async with self._hydrate_lock:
            if not self.hydrated:
                self.hydrate(await load_watches())

    async def sync(self, source_id: str, shared: bool) -> None:
        """Bring one source's state up to date before a batch is proven against it.
        Call under ``lock(source_id)``. Raises (the caller must not prove the batch)
        while the state its blocked set depends on cannot be read."""
        await self.ensure_hydrated()
        await self.retry_unpersisted(source_id)
        if shared or source_id in self._stale:
            # BUG-467: another replica may have opened, kept or rolled back a watch.
            self.hydrate(await load_watches(source_id), source_id)
            self._stale.discard(source_id)

    def hydrate(self, rows: List[RepairWatch], source_id: Optional[str] = None) -> None:
        """Load the durable state (all sources at startup, or one source's shared view).

        The reloaded sources are rebuilt from the DB, so a rollback an operator cleared
        on another replica is lifted here too. Memory may only be ahead of the DB on the
        side of caution: a rollback whose write is still pending stays in force."""
        if source_id is None:
            self.hydrated = True
            reloaded = set(self._watches) | set(self._kept) | set(self._rolled_back)
        else:
            reloaded = {source_id}
        for sid in reloaded:
            self._watches.pop(sid, None)
            self._kept.pop(sid, None)
            self._rolled_back.pop(sid, None)
            self._rollback_times.pop(sid, None)
        for sid, pending in self._unpersisted.items():
            if source_id is None or sid == source_id:
                for w in pending:
                    self._note_rollback(w)
        for row in rows:
            if row.status == CLEARED:
                continue
            w = Watch(
                id=row.id, source_id=row.source_id, recovery_id=row.recovery_id,
                signature=(row.repair_type, row.column_name, row.param),
                batches_required=row.batches_required, proof=dict(row.proof or {}),
                batches_seen=row.batches_seen, status=row.status, reason=row.reason,
                decided_at=row.decided_at,
            )
            if w.status == ROLLED_BACK:
                self._watches.get(w.source_id, {}).pop(w.signature, None)
                self._kept.get(w.source_id, set()).discard(w.signature)
                self._note_rollback(w)
            elif w.signature in self._rolled_back.get(w.source_id, {}) and not (
                    _utc(row.created_at) is not None
                    and _utc(row.created_at) >= self.block_until(w.source_id, w.signature)):
                # Only a watch opened after the block expired counts; one opened while
                # it held (another replica's) must not bypass it.
                continue
            elif w.status == KEPT:
                self._kept.setdefault(w.source_id, set()).add(w.signature)
            else:
                self._watches.setdefault(w.source_id, {})[w.signature] = w

    async def observe(
        self, source_id: str, evidence: Optional[Dict[str, Any]],
        recovery_id: Optional[str], repairs: List[Any],
    ) -> Outcome:
        """Judge the source's open watches on this batch, then open watches for the
        repairs it applied that are new to the source. Call under ``lock(source_id)``.

        ``evidence`` is None for a batch with no drift. A watch opened by this batch
        is not judged on it: that batch was the repair's sandbox (safety net step 1),
        and the canary is the next N batches.
        """
        out = Outcome()
        now = datetime.now(timezone.utc)
        verdicts = [(w, judge(w.signature, evidence)) for w in self.watches(source_id)]
        out.rolled_back = [
            replace(w, status=ROLLED_BACK, decided_at=now, reason=(
                f"batch {w.batches_seen + 1} after the repair drifted on column "
                f"'{w.signature[1]}' and no proven repair brought it back to the baseline"))
            for w, verdict in verdicts if verdict == REGRESSED]
        lost_rollbacks: List[Watch] = []
        if out.rolled_back:
            written = await _persist(out.rolled_back)
            # In force here whatever the write did: re-applying a repair the canary
            # rejected would be the worse error.
            for w in out.rolled_back:
                self._watches[source_id].pop(w.signature, None)
                self._note_rollback(w)
                logger.warning("post-heal canary rolled back %s for %s: %s", w.signature, source_id, w.reason)
            if written is None:
                # Review of 2ce9d08: the DB still says 'watching', so a restart or
                # another replica would apply the repair again. Nothing about this
                # source is answered until the row is written (sync retries it first),
                # and this batch counts for no other watch.
                self._unpersisted.setdefault(source_id, []).extend(out.rolled_back)
                raise RollbackNotPersisted(
                    f"the rollback of {len(out.rolled_back)} repair(s) for {source_id} "
                    "could not be written")
            lost_rollbacks = written
        for w, verdict in verdicts:
            if verdict != CLEAN:
                continue
            w.batches_seen += 1
            if w.batches_seen >= w.batches_required:
                w.status, w.decided_at = KEPT, now
                out.kept.append(w)
            else:
                out.advanced.append(w)
        for w in out.kept:
            self._watches[source_id].pop(w.signature, None)
            self._kept.setdefault(source_id, set()).add(w.signature)

        if self.batches_required > 0 and recovery_id is not None:
            # An expired block is not known: the repair was proven again on this batch,
            # so it opens a new watch and passes the canary again.
            known = (set(self._watches.get(source_id, {})) | self._kept.get(source_id, set())
                     | self.blocked(source_id, now))
            for r in repairs:
                sig = repair_signature(r.type, r.column, r.proof)
                if sig is None or sig in known:
                    continue
                w = Watch(source_id=source_id, recovery_id=recovery_id, signature=sig,
                          batches_required=self.batches_required, proof=dict(r.proof))
                self._watches.setdefault(source_id, {})[sig] = w
                known.add(sig)
                out.opened.append(w)
        lost = list(lost_rollbacks)
        if out.kept or out.advanced or out.opened:
            # Progress, unlike a rollback, may lag the DB safely: a restart then re-runs
            # part of a watch, it never skips one.
            lost += await _persist(out.kept + out.advanced + out.opened) or []
        if lost:
            # Another replica moved these watches on (above all: rolled one back, or an
            # operator cleared a rollback) after this one read them. The DB wins;
            # reload the source from it.
            try:
                self.hydrate(await load_watches(source_id), source_id)
            except Exception as exc:
                logger.error("could not reload canary state of %s after a lost write: %s",
                             source_id, exc)
                self._stale.add(source_id)
        return out

    async def retry_unpersisted(self, source_id: str) -> None:
        """Write the source's pending rollbacks; raises while they cannot be written."""
        pending = self._unpersisted.get(source_id)
        if not pending:
            return
        if await _persist(pending) is None:
            raise RollbackNotPersisted(
                f"the rollback of {len(pending)} repair(s) for {source_id} is still not written")
        del self._unpersisted[source_id]

    async def clear(
        self, source_id: str, signature: Signature, by: str, note: Optional[str],
    ) -> Optional[Watch]:
        """An operator lifts a rollback: its row becomes ``cleared`` (kept as the audit
        trail) and the repair may be proven, and watched, afresh. Call under
        ``lock(source_id)`` after ``sync``. Returns None when the source has no such
        rollback in the DB; raises when the DB cannot be written (the rollback stays)."""
        w = self._rolled_back.get(source_id, {}).get(signature)
        if w is None:
            return None
        cleared = replace(w, status=CLEARED, decided_at=datetime.now(timezone.utc),
                          reason=f"{w.reason}; rollback cleared by {by}" + (f": {note}" if note else ""))
        lost = await _persist([cleared])
        if lost is None:
            raise RuntimeError(f"could not write the cleared rollback of {signature} for {source_id}")
        if lost:
            # The DB no longer says ROLLED_BACK (another replica cleared it first): it wins.
            self.hydrate(await load_watches(source_id), source_id)
            return None
        del self._rolled_back[source_id][signature]
        self._rollback_times.get(source_id, {}).pop(signature, None)
        logger.warning("post-heal canary rollback of %s for %s cleared by %s", signature, source_id, by)
        return cleared

    def unpersisted(self, source_id: str) -> List[Watch]:
        return list(self._unpersisted.get(source_id, []))


# The states a write may move a stored watch out of. A watch's status only ever moves
# forward (watching -> deployed | rolled_back, deployed -> rolled_back), and a rollback
# is final: no write may overwrite it (review of 2ce9d08: with the shared registry two
# replicas can judge the same watch, and a blind merge let the one that judged it
# clean write "watching" over the other's ROLLED_BACK, resurrecting the repair).
_WRITABLE_FROM = {
    WATCHING: (WATCHING,),
    KEPT: (WATCHING, KEPT),
    ROLLED_BACK: (WATCHING, KEPT),
    CLEARED: (ROLLED_BACK,),
}


async def _persist(watches: List[Watch]) -> Optional[List[Watch]]:
    """Write the watches in one transaction, each as a compare-and-set on its stored
    state. Returns the watches whose write lost to a newer stored state (the caller
    must reload them), or None (logged) when the write failed."""
    try:
        lost: List[Watch] = []
        async with get_session_factory()() as db:
            for w in watches:
                kind, column, param = w.signature
                values = dict(
                    source_id=w.source_id, recovery_id=w.recovery_id,
                    repair_type=kind, column_name=column, param=param, proof=w.proof,
                    status=w.status, batches_seen=w.batches_seen,
                    batches_required=w.batches_required, reason=w.reason,
                    decided_at=w.decided_at,
                )
                guard = [RepairWatch.id == w.id, RepairWatch.status.in_(_WRITABLE_FROM[w.status])]
                if w.status == WATCHING:
                    # Progress never goes backwards either: a replica that read an
                    # older count reloads instead of erasing another's clean batches.
                    guard.append(RepairWatch.batches_seen <= w.batches_seen)
                res = await db.execute(
                    update(RepairWatch).where(*guard).values(**values)
                    .execution_options(synchronize_session=False))
                if res.rowcount:
                    continue
                stored = (await db.execute(
                    select(RepairWatch.status).where(RepairWatch.id == w.id))).scalar_one_or_none()
                if stored is None:
                    db.add(RepairWatch(id=w.id, **values))
                    await db.flush()
                elif not (w.status == ROLLED_BACK and stored == ROLLED_BACK):
                    lost.append(w)
            await db.commit()
        return lost
    except Exception as exc:
        logger.error("could not persist %d repair watch(es): %s", len(watches), exc)
        return None


async def load_watches(source_id: Optional[str] = None) -> List[RepairWatch]:
    async with get_session_factory()() as db:
        stmt = select(RepairWatch).order_by(RepairWatch.created_at.asc())
        if source_id is not None:
            stmt = stmt.where(RepairWatch.source_id == source_id)
        return list((await db.execute(stmt)).scalars().all())
