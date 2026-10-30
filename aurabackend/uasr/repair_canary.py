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
* regressed -- the batch drifted on the repair's own column and the repair does not
  bring it back: its rename is contradicted, its type change re-fires and it is not
  proven, the column is still off-baseline after it;
* inconclusive -- an earlier fault the batch could not prove stopped the proof before
  the repair's stage, so the batch says nothing about it.

N clean batches (``UASR_POST_HEAL_VALIDATION_BATCHES``, default 3) keep the repair;
one regression rolls it back: the repair is blocked for the source, so a batch it
would apply to is quarantined instead, and its rows never reach a caller again. Drift
on other columns is not the repair's regression and never rolls it back; the proof
handles it on its own (quarantine, alert).

One watch per repair, not per source: a second repair for the source opens its own
watch and leaves the first one's running. The watch, the rollback and the kept repair
live in ``uasr_repair_watches``; a rollback is written there before it takes effect
in memory, so a restart can never bring a rolled-back repair back.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from sqlalchemy import select

from .db import get_session_factory
from .models import RecoveryStatus, RepairWatch
from .tenancy import owns_source

logger = logging.getLogger("uasr.repair_canary")

Signature = Tuple[str, str, str]

CLEAN, REGRESSED, INCONCLUSIVE = "clean", "regressed", "inconclusive"
WATCHING = "watching"
KEPT = RecoveryStatus.DEPLOYED.value
ROLLED_BACK = RecoveryStatus.ROLLED_BACK.value


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
    return REGRESSED if column in seen else CLEAN


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
        # Rollbacks in force in memory whose ROLLED_BACK row could not be written yet.
        self._unpersisted: Dict[str, List[Watch]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def lock(self, source_id: str) -> asyncio.Lock:
        return self._locks.setdefault(source_id, asyncio.Lock())

    def blocked(self, source_id: str) -> FrozenSet[Signature]:
        return frozenset(self._rolled_back.get(source_id, {}))

    def watches(self, source_id: str) -> List[Watch]:
        return list(self._watches.get(source_id, {}).values())

    def visible(self, tenant: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
        """The canary state of ``tenant``'s sources only (BUG-263)."""
        def own(by_source: Dict[str, Dict[Signature, Watch]]) -> List[Dict[str, Any]]:
            return [w.to_dict() for sid, ws in by_source.items() if owns_source(tenant, sid)
                    for w in ws.values()]
        return {
            "watching": own(self._watches),
            "rolled_back": own(self._rolled_back),
            "unpersisted_rollbacks": sum(
                len(ws) for sid, ws in self._unpersisted.items() if owns_source(tenant, sid)),
        }

    def hydrate(self, rows: List[RepairWatch], source_id: Optional[str] = None) -> None:
        """Load the durable state (all sources at startup, or one source's shared view).

        A rollback is never undone by a reload: memory may only be ahead of the DB on
        the side of caution, by a rollback whose write is still pending."""
        if source_id is not None:
            self._watches.pop(source_id, None)
        for row in rows:
            w = Watch(
                id=row.id, source_id=row.source_id, recovery_id=row.recovery_id,
                signature=(row.repair_type, row.column_name, row.param),
                batches_required=row.batches_required, proof=dict(row.proof or {}),
                batches_seen=row.batches_seen, status=row.status, reason=row.reason,
                decided_at=row.decided_at,
            )
            if w.status == ROLLED_BACK:
                self._watches.get(w.source_id, {}).pop(w.signature, None)
                self._rolled_back.setdefault(w.source_id, {})[w.signature] = w
            elif w.signature in self._rolled_back.get(w.source_id, {}):
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
        for w in self.watches(source_id):
            verdict = judge(w.signature, evidence)
            if verdict == REGRESSED:
                w.status, w.decided_at = ROLLED_BACK, now
                w.reason = (
                    f"batch {w.batches_seen + 1} after the repair drifted on column "
                    f"'{w.signature[1]}' and the repair did not bring it back to the baseline")
                out.rolled_back.append(w)
            elif verdict == CLEAN:
                w.batches_seen += 1
                if w.batches_seen >= w.batches_required:
                    w.status, w.decided_at = KEPT, now
                    out.kept.append(w)
                else:
                    out.advanced.append(w)
        if out.rolled_back:
            # The DB says ROLLED_BACK before memory acts on it, so no reload can bring
            # the repair back. If the write fails the rollback still takes effect here
            # (re-applying a repair the canary rejected would be the worse error) and
            # the write is retried before this source's next batch.
            if not await _persist(out.rolled_back):
                self._unpersisted.setdefault(source_id, []).extend(out.rolled_back)
        for w in out.rolled_back:
            self._watches[source_id].pop(w.signature, None)
            self._rolled_back.setdefault(source_id, {})[w.signature] = w
            logger.warning("post-heal canary rolled back %s for %s: %s", w.signature, source_id, w.reason)
        for w in out.kept:
            self._watches[source_id].pop(w.signature, None)
            self._kept.setdefault(source_id, set()).add(w.signature)

        if self.batches_required > 0 and recovery_id is not None:
            known = (set(self._watches.get(source_id, {})) | self._kept.get(source_id, set())
                     | set(self._rolled_back.get(source_id, {})))
            for r in repairs:
                sig = repair_signature(r.type, r.column, r.proof)
                if sig is None or sig in known:
                    continue
                w = Watch(source_id=source_id, recovery_id=recovery_id, signature=sig,
                          batches_required=self.batches_required, proof=dict(r.proof))
                self._watches.setdefault(source_id, {})[sig] = w
                known.add(sig)
                out.opened.append(w)
        if out.kept or out.advanced or out.opened:
            # Progress, unlike a rollback, may lag the DB safely: a restart then re-runs
            # part of a watch, it never skips one.
            await _persist(out.kept + out.advanced + out.opened)
        return out

    async def retry_unpersisted(self, source_id: str) -> None:
        pending = self._unpersisted.get(source_id)
        if pending and await _persist(pending):
            del self._unpersisted[source_id]

    def unpersisted(self, source_id: str) -> List[Watch]:
        return list(self._unpersisted.get(source_id, []))


async def _persist(watches: List[Watch]) -> bool:
    """Upsert the watches in one transaction; False (logged) when the write failed."""
    try:
        async with get_session_factory()() as db:
            for w in watches:
                kind, column, param = w.signature
                await db.merge(RepairWatch(
                    id=w.id, source_id=w.source_id, recovery_id=w.recovery_id,
                    repair_type=kind, column_name=column, param=param, proof=w.proof,
                    status=w.status, batches_seen=w.batches_seen,
                    batches_required=w.batches_required, reason=w.reason,
                    decided_at=w.decided_at,
                ))
            await db.commit()
        return True
    except Exception as exc:
        logger.error("could not persist %d repair watch(es): %s", len(watches), exc)
        return False


async def load_watches(source_id: Optional[str] = None) -> List[RepairWatch]:
    async with get_session_factory()() as db:
        stmt = select(RepairWatch).order_by(RepairWatch.created_at.asc())
        if source_id is not None:
            stmt = stmt.where(RepairWatch.source_id == source_id)
        return list((await db.execute(stmt)).scalars().all())
