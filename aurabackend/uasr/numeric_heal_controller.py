"""Phase-2 verified auto-commit for numeric semantic healing.

Phase 1 (``numeric_semantics``) *proposes* a numeric repair per batch but never
applies it. Committing on a single batch is unsafe: one batch can look healed by
coincidence, and a committed transform then applies to *every* later batch. This
module closes the loop with a gate that makes an auto-commit safe by construction.

The gate is a per-(source, column) state machine:

    OBSERVING ──drift+candidate──▶ CANARY ──K-of-K verified──▶ COMMITTED
        ▲                            │                              │
        └──────── aborted ◀──────────┘                              │
        └──────────────── raw stream healthy / over-correct ◀───────┘  (revert)

Two safety properties, both proven necessary by the real-data gate
(0 false heals / 672 regime-change batches):

1. **Sequential verification.** A proposed transform T does not commit on the
   batch that first suggested it. It opens a *canary* and must independently
   re-earn acceptance on ``k_confirm`` consecutive batches — each time the raw
   batch must be genuinely drifted, T must be the argmin transform over the
   identity *and* every alternative, and the transformed column must re-project
   into the healthy z-band. Any inconsistency resets the canary. A legitimate
   regime change (mean shift / variance change) has no multiplicative inverse
   that re-projects onto the baseline, so it can never accumulate K
   confirmations — it is alerted, never healed.

2. **Two-sided commit.** After commit the controller keeps scoring the *raw*
   (untransformed) stream. If raw batches become healthy again (upstream fixed
   the bug) or the committed transform starts over-correcting, the transform is
   auto-reverted. A committed division can therefore never corrupt a stream that
   stopped being corrupted.

Every state transition (commit / abort / revert) writes a canonical-JSON +
SHA-256 audit record using the same contract as the counterfactual engine, so
existing replay/verify tooling works against these decisions.

numpy-only; no I/O; deterministic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np

from .numeric_semantics import TRANSFORMS, NumericBaseline, best_transform

_DEFAULT_DRIFT_K = 3.0            # z > k * healthy_z ⇒ drifted (matches analyzer)
_DEFAULT_ACCEPT_RATIO = 0.5      # healed z must drop to <= ratio * z_raw
_DEFAULT_K_CONFIRM = 3           # consecutive confirmations required to commit
_DEFAULT_REVERT_PATIENCE = 3     # consecutive healthy-raw batches ⇒ revert


class HealState(str, Enum):
    OBSERVING = "observing"
    CANARY = "canary"
    COMMITTED = "committed"


@dataclass
class HealAudit:
    """Auditable record of a controller state transition."""
    record_id: str
    audit_record_hash: str
    source_id: str
    column: str
    event: str                    # "canary_open" | "commit" | "abort" | "revert"
    transform: str
    factor: float
    timestamp_iso: str
    detail: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "uasr.numeric_heal.v1"


@dataclass
class _ColumnController:
    """Mutable per-column state within one source."""
    baseline: NumericBaseline
    state: HealState = HealState.OBSERVING
    # canary bookkeeping
    candidate_transform: str = "none"
    candidate_factor: float = 1.0
    confirmations: int = 0
    # commit bookkeeping
    committed_transform: str = "none"
    committed_factor: float = 1.0
    healthy_raw_streak: int = 0


@dataclass
class HealDecision:
    """What the controller decided for one column on one batch."""
    column: str
    state: HealState
    raw_drifted: bool
    z_raw: float
    applied_transform: str        # transform actually applied to output ("none" if pass-through)
    applied_factor: float
    event: Optional[str] = None   # transition that fired this batch, if any
    confirmations: int = 0
    audit: Optional[HealAudit] = None


class NumericHealController:
    """Sequential-verification, two-sided commit gate for numeric repairs.

    One instance manages many ``(source_id, column)`` pairs. Baselines are the
    same serializable ``NumericBaseline`` used by the analyzer, so they can be
    recovered from the shared StateStore and injected via ``load_baseline``.

    Usage per batch::

        decision = ctrl.observe(source_id, column_name, values)
        healed_values = ctrl.apply(source_id, column_name, values)

    ``observe`` advances the state machine and returns a ``HealDecision``;
    ``apply`` returns the column with the currently-committed transform applied
    (a no-op pass-through unless the column is COMMITTED).
    """

    def __init__(
        self,
        drift_k: float = _DEFAULT_DRIFT_K,
        accept_ratio: float = _DEFAULT_ACCEPT_RATIO,
        k_confirm: int = _DEFAULT_K_CONFIRM,
        revert_patience: int = _DEFAULT_REVERT_PATIENCE,
    ) -> None:
        self._drift_k = drift_k
        self._accept_ratio = accept_ratio
        self._k_confirm = max(1, int(k_confirm))
        self._revert_patience = max(1, int(revert_patience))
        self._ctrls: Dict[str, _ColumnController] = {}
        self._audit_log: List[HealAudit] = []

    # ── baseline management ──────────────────────────────────────────────
    def load_baseline(self, source_id: str, column: str, baseline: NumericBaseline) -> None:
        key = self._key(source_id, column)
        c = self._ctrls.get(key)
        if c is None:
            self._ctrls[key] = _ColumnController(baseline=baseline)
        else:
            c.baseline = baseline

    def has_baseline(self, source_id: str, column: str) -> bool:
        return self._key(source_id, column) in self._ctrls

    def state_of(self, source_id: str, column: str) -> Optional[HealState]:
        c = self._ctrls.get(self._key(source_id, column))
        return c.state if c else None

    @property
    def audit_log(self) -> List[HealAudit]:
        return list(self._audit_log)

    # ── core gate ────────────────────────────────────────────────────────
    def observe(self, source_id: str, column: str, values) -> HealDecision:
        """Advance the state machine for one column on one raw batch."""
        key = self._key(source_id, column)
        c = self._ctrls.get(key)
        if c is None:
            # No baseline ⇒ nothing to gate against; pass through.
            return HealDecision(column=column, state=HealState.OBSERVING,
                                raw_drifted=False, z_raw=0.0,
                                applied_transform="none", applied_factor=1.0)

        col = np.asarray(values, dtype=float)
        z_raw = c.baseline.z_distance(col)
        thresh = self._drift_k * max(c.baseline.healthy_z, 1e-9)
        raw_drifted = z_raw > thresh

        # Two heal predicates over the best inverse transform:
        #   * heals_ratio — z drops by at least `accept_ratio` (near-total for a
        #     genuine unit error: z_after≈few vs z0≈1e9). This alone separates a
        #     unit/scale bug from a regime change, because no power-of-ten factor
        #     reduces a pure mean/variance shift (best_transform returns "none").
        #   * heals_band  — the transformed column additionally lands back inside
        #     the healthy z-band. Guards against near-miss scale factors (e.g. a
        #     ×150 shift where ÷100 improves but does not truly recover). Used
        #     only on ENTRY; the 16-D distance is noisy batch-to-batch, so it is
        #     not re-required on every canary confirmation (sequential
        #     verification absorbs that noise instead).
        name, factor, z0, z_after = best_transform(col, c.baseline)
        heals_ratio = name != "none" and z_after <= self._accept_ratio * z0
        heals_band = heals_ratio and z_after <= thresh

        if c.state is HealState.OBSERVING:
            return self._step_observing(c, source_id, column, raw_drifted,
                                        z_raw, name, factor, heals_band)
        if c.state is HealState.CANARY:
            return self._step_canary(c, source_id, column, raw_drifted,
                                     z_raw, name, factor, heals_ratio)
        return self._step_committed(c, source_id, column, raw_drifted, z_raw)

    def _step_observing(self, c, source_id, column, raw_drifted, z_raw,
                        name, factor, heals) -> HealDecision:
        if raw_drifted and heals:
            c.candidate_transform = name
            c.candidate_factor = factor
            c.confirmations = 1
            # Opening the canary is confirmation #1. If a single confirmation
            # already satisfies k_confirm, commit on entry; otherwise enter the
            # canary and gather the rest.
            if c.confirmations >= self._k_confirm:
                c.state = HealState.COMMITTED
                c.committed_transform = name
                c.committed_factor = factor
                c.healthy_raw_streak = 0
                audit = self._emit(source_id, column, "commit", name, factor,
                                   {"confirmations": c.confirmations,
                                    "z_raw": round(z_raw, 6)})
                return HealDecision(column=column, state=c.state, raw_drifted=True,
                                    z_raw=z_raw, applied_transform=name,
                                    applied_factor=factor, event="commit",
                                    confirmations=c.confirmations, audit=audit)
            c.state = HealState.CANARY
            audit = self._emit(source_id, column, "canary_open", name, factor,
                               {"z_raw": round(z_raw, 6), "k_confirm": self._k_confirm})
            return HealDecision(column=column, state=c.state, raw_drifted=True,
                                z_raw=z_raw, applied_transform="none",
                                applied_factor=1.0, event="canary_open",
                                confirmations=1, audit=audit)
        # drifted but no multiplicative fix (e.g. regime change) ⇒ alert only,
        # or simply healthy ⇒ nothing to do.
        return HealDecision(column=column, state=HealState.OBSERVING,
                            raw_drifted=raw_drifted, z_raw=z_raw,
                            applied_transform="none", applied_factor=1.0)

    def _step_canary(self, c, source_id, column, raw_drifted, z_raw,
                     name, factor, heals) -> HealDecision:
        consistent = (raw_drifted and heals
                      and name == c.candidate_transform
                      and factor == c.candidate_factor)
        if consistent:
            c.confirmations += 1
            if c.confirmations >= self._k_confirm:
                c.state = HealState.COMMITTED
                c.committed_transform = c.candidate_transform
                c.committed_factor = c.candidate_factor
                c.healthy_raw_streak = 0
                audit = self._emit(source_id, column, "commit",
                                   c.committed_transform, c.committed_factor,
                                   {"confirmations": c.confirmations,
                                    "z_raw": round(z_raw, 6)})
                return HealDecision(column=column, state=c.state, raw_drifted=True,
                                    z_raw=z_raw,
                                    applied_transform=c.committed_transform,
                                    applied_factor=c.committed_factor,
                                    event="commit",
                                    confirmations=c.confirmations, audit=audit)
            # still gathering confirmations — do NOT yet apply
            return HealDecision(column=column, state=c.state, raw_drifted=True,
                                z_raw=z_raw, applied_transform="none",
                                applied_factor=1.0,
                                confirmations=c.confirmations)
        # inconsistent (raw healthy, different transform, or no longer heals):
        # abort the canary, back to observing.
        aborted = c.candidate_transform
        c.state = HealState.OBSERVING
        c.candidate_transform, c.candidate_factor, c.confirmations = "none", 1.0, 0
        audit = self._emit(source_id, column, "abort", aborted, 1.0,
                           {"reason": "canary confirmation broke",
                            "raw_drifted": raw_drifted, "z_raw": round(z_raw, 6)})
        return HealDecision(column=column, state=HealState.OBSERVING,
                            raw_drifted=raw_drifted, z_raw=z_raw,
                            applied_transform="none", applied_factor=1.0,
                            event="abort", audit=audit)

    def _step_committed(self, c, source_id, column, raw_drifted, z_raw) -> HealDecision:
        # Two-sided guard: if the RAW stream is healthy again, the upstream bug
        # is gone — reverting the transform (which would now corrupt good data).
        if not raw_drifted:
            c.healthy_raw_streak += 1
            if c.healthy_raw_streak >= self._revert_patience:
                reverted = c.committed_transform
                c.state = HealState.OBSERVING
                c.committed_transform, c.committed_factor = "none", 1.0
                c.healthy_raw_streak = 0
                audit = self._emit(source_id, column, "revert", reverted, 1.0,
                                   {"reason": "raw stream healthy again",
                                    "patience": self._revert_patience,
                                    "z_raw": round(z_raw, 6)})
                return HealDecision(column=column, state=HealState.OBSERVING,
                                    raw_drifted=False, z_raw=z_raw,
                                    applied_transform="none", applied_factor=1.0,
                                    event="revert", audit=audit)
            # provisional: still applying, but counting toward revert
            return HealDecision(column=column, state=HealState.COMMITTED,
                                raw_drifted=False, z_raw=z_raw,
                                applied_transform=c.committed_transform,
                                applied_factor=c.committed_factor)
        # raw still drifted ⇒ keep applying, reset the healthy streak
        c.healthy_raw_streak = 0
        return HealDecision(column=column, state=HealState.COMMITTED,
                            raw_drifted=True, z_raw=z_raw,
                            applied_transform=c.committed_transform,
                            applied_factor=c.committed_factor)

    def apply(self, source_id: str, column: str, values):
        """Return ``values`` with the currently-committed transform applied.

        Pass-through (returns a float array copy) unless the column is COMMITTED.
        """
        c = self._ctrls.get(self._key(source_id, column))
        col = np.asarray(values, dtype=float)
        if c is None or c.state is not HealState.COMMITTED:
            return col
        return col * c.committed_factor

    # ── internals ──────────────────────────────────────────────────────────
    @staticmethod
    def _key(source_id: str, column: str) -> str:
        return f"{source_id}\x00{column}"

    def _emit(self, source_id, column, event, transform, factor, detail) -> HealAudit:
        from counterfactual_service.canonical import sha256_canonical
        payload = {
            "schema_version": "uasr.numeric_heal.v1",
            "source_id": source_id,
            "column": column,
            "event": event,
            "transform": transform,
            "factor": factor,
            "detail": detail,
        }
        audit = HealAudit(
            record_id=f"uasr_heal_{uuid.uuid4().hex[:12]}",
            audit_record_hash=sha256_canonical(payload),
            source_id=source_id,
            column=column,
            event=event,
            transform=transform,
            factor=factor,
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            detail=detail,
        )
        self._audit_log.append(audit)
        return audit


__all__ = [
    "NumericHealController",
    "HealState",
    "HealDecision",
    "HealAudit",
]
