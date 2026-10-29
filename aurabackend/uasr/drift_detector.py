"""
UASR Latent Drift Detector
============================
Monitors the statistical manifold of incoming data batches.

Detection methods:
  1. Schema drift  — column additions / removals / type changes
  2. Statistical drift — KL-Divergence between batch P and baseline Q
  3. Semantic drift  — cosine distance between batch embedding and reference matrix

KL Divergence:
    D_KL(P || Q) = Σ P(x) · log(P(x) / Q(x))

Dynamic threshold ζ adapts based on historical variance of D_KL values.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import zlib
from collections import Counter
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .models import (
    BatchPayload,
    ColumnDistribution,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    schema_types_match,
)
from .state_store import InMemoryStateStore, SourceState, StateStore

logger = logging.getLogger("uasr.drift_detector")

# Small constant to prevent log(0)
_EPS = 1e-10

# Divergence reported when a column is numeric on one side and categorical on the other.
_KIND_CHANGE_KL = -math.log(_EPS)

# Default bins for continuous-valued histograms
_DEFAULT_BINS = 50

# BUG-564: rows of the smaller sample per merged bin when comparing a batch
# with the baseline on the baseline's edges -- see _aligned_kl.
_MIN_ROWS_PER_BIN = 20

# BUG-568 data-quality thresholds -- see the "Data-quality drift" section.
_DQ_Z = 5.0                      # z-score a null-rate rise must reach
_NULL_MIN_RISE = 0.05            # ...and its minimum absolute rise
_VOLUME_FACTOR = 100             # row-count ratio that counts as a volume fault
_VOLUME_MIN_BASELINE_ROWS = 100  # too few baseline rows to judge volume below this


def _is_complete_run(batch: BatchPayload) -> bool:
    """BUG-568 review: the batch declares it carries a whole run or period."""
    return (batch.metadata or {}).get("complete_run") is True


class DriftDetector:
    """
    Stateful detector that compares incoming batches against stored baselines.

    Usage:
        detector = DriftDetector()
        detector.register_baseline("src_1", baseline_distributions)
        result = detector.detect(batch)
    """

    def __init__(
        self,
        default_zeta: float = 0.15,
        schema_strict: bool = True,
        semantic_threshold: float = 0.25,
        state_store: Optional[StateStore] = None,
        warmup_batches: int = 5,
        schema_intent_enabled: bool = False,
    ) -> None:
        # ζ — KL-divergence threshold.  Adapted dynamically per source.
        self._default_zeta = default_zeta
        self._schema_strict = schema_strict
        self._semantic_threshold = semantic_threshold
        # Opt-in (UASR_SCHEMA_INTENT_ENABLED): when on, a declared
        # schema-change intent (declare_schema_intent) that covers the
        # observed added/removed/retyped columns is adopted as the new
        # baseline instead of raising drift. Off by default -- unchanged
        # behaviour for every existing deployment.
        self._schema_intent_enabled = schema_intent_enabled
        # Cold-start suppression: the adaptive ζ needs ≥5 KL samples before
        # it reflects a source's real noise floor.  Until then, a 50-bin
        # histogram estimated off ~500 rows carries enough Poisson bin-noise
        # to push KL past the flat default ζ and fire false alarms.  For the
        # first ``warmup_batches`` numeric batches per source we still RECORD
        # the KL sample (so the window fills) but SUPPRESS the statistical
        # alert.  Set to 0 to disable and restore the original behaviour.
        self._warmup_batches = max(0, int(warmup_batches))

        # Per-source state (baselines, KL history, schema baselines,
        # reference embeddings) lives behind a pluggable StateStore so the
        # detector can scale horizontally.  The default is an unbounded
        # in-process store, which is BIT-IDENTICAL to the original four
        # loose dicts.  Pass ``InMemoryStateStore(capacity=N)`` to bound
        # memory with LRU eviction, or ``RedisStateStore(...)`` to share
        # state across worker replicas (resolves the cross-replica
        # cold-miss on Kafka partition rebalance).
        # NB: explicit ``is None`` — an empty InMemoryStateStore is falsy
        # (it defines __len__), so ``state_store or ...`` would discard a
        # caller-supplied empty store.
        self._store: StateStore = (
            state_store if state_store is not None else InMemoryStateStore()
        )

        # BUG-063/BUG-064: every load-mutate-save sequence below (register_
        # baseline, register_reference_embedding, declare_schema_intent,
        # detect) used to run with no lock across the round-trip, while
        # service.py's HTTP handlers dispatch these calls via
        # asyncio.to_thread -- real OS threads, not serialized by the event
        # loop. Two concurrent calls for the SAME source_id (a first-touch
        # race on a brand-new source, or two detect() calls on an existing
        # one) could each load a pre-update SourceState, mutate their own
        # copy, and save -- the second save silently clobbers the first,
        # losing a baseline/schema write or a KL-history sample. A per-
        # source threading.Lock (not asyncio.Lock: these calls run on
        # worker threads, not coroutines) serializes the round-trip per
        # source_id without serializing unrelated sources against each
        # other. The lock dict itself is guarded so two threads creating a
        # lock for the same brand-new source_id at once can't each get a
        # different Lock object.
        self._source_locks: Dict[str, threading.Lock] = {}
        self._source_locks_guard = threading.Lock()

    def _lock_for(self, source_id: str) -> threading.Lock:
        lock = self._source_locks.get(source_id)
        if lock is None:
            with self._source_locks_guard:
                lock = self._source_locks.setdefault(source_id, threading.Lock())
        return lock

    # ────────────────────────────────────────────────────────────────
    # Back-compat read-only views
    # ------------------------------------------------------------------
    # Historically the detector exposed four public-ish dicts
    # (``_baselines`` etc.).  A handful of callers (health/list endpoints
    # in service.py, poisoning tests) read them.  We keep those working by
    # rebuilding read-only snapshots from the store.  Reads use ``peek`` so
    # they do NOT perturb LRU recency.  These are VIEWS — mutate state via
    # register_baseline / detect, not through these.
    # ────────────────────────────────────────────────────────────────
    @property
    def _baselines(self) -> Dict[str, Dict[str, ColumnDistribution]]:
        out: Dict[str, Dict[str, ColumnDistribution]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.baseline is not None:
                out[sid] = st.baseline
        return out

    @property
    def _schema_baselines(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.schema is not None:
                out[sid] = st.schema
        return out

    @property
    def _reference_embeddings(self) -> Dict[str, List[List[float]]]:
        out: Dict[str, List[List[float]]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.embeddings:
                out[sid] = st.embeddings
        return out

    @property
    def _kl_history(self) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for sid in self._store.source_ids():
            st = self._store.peek(sid)
            if st.kl_history:
                out[sid] = st.kl_history
        return out

    # ────────────────────────────────────────────────────────────────
    # Baseline management
    # ────────────────────────────────────────────────────────────────

    def register_baseline(
        self,
        source_id: str,
        distributions_or_batch: "Dict[str, ColumnDistribution] | BatchPayload",
        schema: Optional[Dict[str, str]] = None,
    ) -> None:
        """Store a known-good distribution profile as the reference.

        Accepts either a pre-computed distribution dict or a raw
        ``BatchPayload`` (in which case distributions, schema and the
        reference embedding are computed automatically).
        """
        baseline_rows: Optional[Dict[str, Any]] = None
        if isinstance(distributions_or_batch, BatchPayload):
            batch = distributions_or_batch
            distributions = self._compute_distributions(batch)
            if not schema:
                schema = batch.schema_snapshot or (
                    {c: "unknown" for c in batch.columns} if batch.columns else None
                )
            # Also register a reference embedding so semantic drift works
            emb = self._compute_batch_embedding(batch)
            if emb:
                self.register_reference_embedding(source_id, emb)
            if batch.rows:
                cols = batch.columns or list(batch.rows[0].keys())
                baseline_rows = {
                    "rows": len(batch.rows),
                    "columns": list(cols),
                    "equal_pairs": self._equal_row_pairs(batch, cols),
                    # BUG-568 review: only a baseline that is itself a whole
                    # run can anchor the volume check -- see _check_volume_drift.
                    "complete_run": _is_complete_run(batch),
                }
        else:
            distributions = distributions_or_batch

        with self._lock_for(source_id):
            st = self._store.load(source_id)
            st.baseline = distributions
            # BUG-568: a distributions-only re-baseline (mapek_worker) carries
            # no row-level facts, so the previous batch's duplicate profile is
            # dropped rather than compared against a baseline it no longer
            # describes (e.g. a column removed since makes rows less unique).
            st.baseline_rows = baseline_rows
            if schema:
                st.schema = schema
            self._store.save(source_id, st)
        logger.info("Registered baseline for source=%s (%d columns)", source_id, len(distributions))

    def baseline_distance(self, source_id: str, batch: "BatchPayload") -> float:
        """DSR-007b: a single continuous drift-distance score for a batch,
        independent of which detector (classical or martingale) actually
        fired.

        Mean absolute standardized distance
        ``|batch_mean - baseline_mean| / max(baseline_std, eps)`` across
        every numeric column that has a registered baseline with a nonzero
        sample count. Returns 0.0 when no baseline is registered yet, or
        when no column overlaps -- callers get a real number always, never
        None, so it can feed a canary outcome-history record unconditionally.

        Deliberately reuses the summary-stat ``ColumnDistribution`` already
        stored by ``register_baseline`` rather than retaining raw samples
        (which the martingale channel does, at real per-source memory cost)
        -- this is a coarser signal, but "coarse and always available" is
        what DSR-007a/b need for a canary outcome, not a statistical test.
        """
        baseline = self._store.peek(source_id).baseline
        if not baseline:
            return 0.0
        col_samples: Dict[str, List[float]] = {}
        for col in batch.columns:
            samples = [
                float(v) for r in batch.rows
                if isinstance((v := r.get(col)), (int, float)) and not isinstance(v, bool)
            ]
            if samples:
                col_samples[col] = samples
        distances: List[float] = []
        for col, samples in col_samples.items():
            dist = baseline.get(col)
            if dist is None or dist.mean is None or dist.std is None:
                continue
            batch_mean = sum(samples) / len(samples)
            std = max(dist.std, 1e-9)
            distances.append(abs(batch_mean - dist.mean) / std)
        return sum(distances) / len(distances) if distances else 0.0

    def has_baseline(self, source_id: str) -> bool:
        """Whether a baseline has ever been registered for this source."""
        return bool(self._store.peek(source_id).baseline)

    def schema_baseline(self, source_id: str) -> Optional[Dict[str, str]]:
        """The schema this source's batches are compared against, if any.

        BUG-563: the first batch a source ever sends is adopted as its schema baseline
        even without a registered distribution baseline (_check_schema_drift), so a
        caller that registers one "only if absent" must keep that schema, not replace
        it with the batch it is registering.
        """
        return self._store.peek(source_id).schema

    def register_reference_embedding(self, source_id: str, embedding: List[float]) -> None:
        """Add a reference embedding vector to the source's context matrix."""
        with self._lock_for(source_id):
            st = self._store.load(source_id)
            st.embeddings.append(embedding)
            self._store.save(source_id, st)

    def declare_schema_intent(
        self,
        source_id: str,
        added: Optional[Dict[str, str]] = None,
        removed: Optional[List[str]] = None,
        type_changes: Optional[Dict[str, str]] = None,
        ttl_seconds: float = 3600.0,
        note: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> None:
        """Declare a sanctioned upcoming schema change for a source.

        Once declared, ``_check_schema_drift`` treats an observed
        added/removed/retyped-column diff as non-drift -- adopting it as
        the new baseline instead -- as long as the diff is fully covered by
        what was declared here and the intent hasn't expired. One active
        intent per source; a new declaration overwrites the previous one.

        No-ops when the feature is off (``schema_intent_enabled=False``) --
        the HTTP boundary (POST /uasr/schema-intent) is expected to gate
        first and fail closed (400), but this keeps the detector itself
        safe to call directly (e.g. from tests) without the flag.
        """
        if not self._schema_intent_enabled:
            return
        with self._lock_for(source_id):
            st = self._store.load(source_id)
            st.schema_intent = {
                "added": dict(added or {}),
                "removed": list(removed or []),
                "type_changes": dict(type_changes or {}),
                "expires_at": time.time() + float(ttl_seconds),
                "note": note,
                "actor": actor,
            }
            self._store.save(source_id, st)
        logger.info(
            "Declared schema intent for source=%s added=%s removed=%s type_changes=%s "
            "ttl_seconds=%.0f actor=%s note=%s",
            source_id,
            list(st.schema_intent["added"]),
            st.schema_intent["removed"],
            list(st.schema_intent["type_changes"]),
            ttl_seconds,
            actor,
            note,
        )

    # ────────────────────────────────────────────────────────────────
    # Main detection
    # ────────────────────────────────────────────────────────────────

    def detect(
        self, batch: BatchPayload, *, record_state: bool = True
    ) -> DriftDetectionResult:
        """Run all drift checks on an incoming batch.

        ``record_state=False`` runs the checks against a disposable copy of
        the source's state and never persists it, so a validation-only call
        (RecoveryLoop re-checking a candidate shim) cannot corrupt
        ``kl_history`` / the adaptive ζ that the live Monitor loop depends on
        for every future event on that source.
        """
        result = DriftDetectionResult(
            source_id=batch.source_id,
            batch_id=batch.batch_id,
        )

        # Load-mutate-save the source's state under its per-source lock
        # (BUG-064) so a concurrently-arriving batch for the SAME source_id
        # (another HTTP request, or the mapek_worker Kafka loop, both
        # dispatched via asyncio.to_thread onto real OS threads) can't
        # interleave with this round-trip and silently lose a KL-history
        # sample or schema-baseline update. Single store round-trip, single
        # LRU touch, threaded through the checks, persisted once at the
        # end.  ``dirty`` tracks whether any check mutated the state
        # (first-seen schema baseline, appended KL sample) so we avoid a
        # redundant write on read-only batches.
        with self._lock_for(batch.source_id):
            st = self._store.load(batch.source_id)
            if not record_state:
                # kl_history is mutated in place (append/trim) by
                # _check_statistical_drift, so a shallow dataclass copy would
                # still share (and corrupt) the same list — copy it explicitly.
                st = replace(st, kl_history=list(st.kl_history))
            dirty = False

            # BUG-568: every check runs and every finding is reported. The
            # first-hit fields (drift_type, severity, drift_vector, details,
            # kl_divergence, cosine_distance) keep the old precedence --
            # schema, statistical, semantic -- so no existing caller sees a
            # different primary drift; the data-quality checks only become
            # primary when none of those fired.
            findings: List[Tuple[DriftType, Dict[str, Any]]] = []

            # 1. Schema drift
            schema_drift, d1 = self._check_schema_drift(batch, st)
            dirty = dirty or d1
            if schema_drift:
                findings.append((DriftType.SCHEMA, schema_drift))

            # 2. Statistical drift (KL-Divergence). After a schema hit it runs
            # on a copy of kl_history: the old early return never recorded a
            # KL sample for a schema-drifted batch, and the adaptive zeta
            # must keep learning only from batches it would have learned from.
            stat_st = replace(st, kl_history=list(st.kl_history)) if schema_drift else st
            stat_drift, d2 = self._check_statistical_drift(batch, stat_st)
            if not schema_drift:
                dirty = dirty or d2
            if stat_drift:
                findings.append((DriftType.STATISTICAL, stat_drift))

            # 3. Semantic drift (embedding distance) — only if embeddings registered
            sem_drift = self._check_semantic_drift(batch, st)
            if sem_drift:
                findings.append((DriftType.SEMANTIC, sem_drift))

            # 4-6. Data-quality drift against the baseline (BUG-568).
            for dq_type, dq_check in (
                (DriftType.NULL_RATE, self._check_null_rate_drift),
                (DriftType.VOLUME, self._check_volume_drift),
                (DriftType.DUPLICATE, self._check_duplicate_drift),
            ):
                dq_drift = dq_check(batch, st)
                if dq_drift:
                    findings.append((dq_type, dq_drift))

            if dirty and record_state:
                self._store.save(batch.source_id, st)

        if not findings:
            result.details = "No drift detected"
            return result

        primary_type, primary = findings[0]
        result.drift_detected = True
        result.drift_type = primary_type
        result.severity = primary["severity"]
        result.affected_columns = primary.get("affected_columns", [])
        result.drift_vector = primary
        result.details = primary.get("details", "")
        if primary_type == DriftType.STATISTICAL:
            result.kl_divergence = primary["max_kl"]
        elif primary_type == DriftType.SEMANTIC:
            result.cosine_distance = primary["cosine_distance"]
        result.drift_types = [t for t, _ in findings]
        result.drift_vectors = {t.value: v for t, v in findings}
        return result

    # ────────────────────────────────────────────────────────────────
    # Schema drift
    # ────────────────────────────────────────────────────────────────

    def _check_schema_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Returns ``(drift_or_None, state_was_mutated)``."""
        baseline_schema = st.schema
        if not baseline_schema:
            # First time — treat batch schema as baseline
            if batch.schema_snapshot:
                st.schema = batch.schema_snapshot
                return None, True
            elif batch.columns:
                st.schema = {c: "unknown" for c in batch.columns}
                return None, True
            return None, False

        current_cols = set(batch.columns) if batch.columns else set()
        if batch.schema_snapshot:
            current_cols = set(batch.schema_snapshot.keys())

        baseline_cols = set(baseline_schema.keys())

        added = current_cols - baseline_cols
        removed = baseline_cols - current_cols

        if not added and not removed:
            # Check type changes if schema_snapshot provided
            if batch.schema_snapshot:
                type_changes = []
                for col, dtype in batch.schema_snapshot.items():
                    if col in baseline_schema and not schema_types_match(baseline_schema[col], dtype):
                        type_changes.append(col)
                if type_changes:
                    type_changed_types = {c: batch.schema_snapshot[c] for c in type_changes}
                    if self._schema_intent_covers(st, {}, set(), type_changed_types):
                        self._adopt_schema_from_intent(batch, st)
                        return None, True
                    return {
                        "type": "type_change",
                        "affected_columns": type_changes,
                        "severity": DriftSeverity.MEDIUM,
                        "details": f"Type changed in columns: {type_changes}",
                        "old_types": {c: baseline_schema[c] for c in type_changes},
                        "new_types": {c: batch.schema_snapshot[c] for c in type_changes},
                    }, False
                # A column the baseline only ever saw as null gets its type from the
                # first batch that carries a value, so later retypes are detectable.
                learned = {
                    c: t for c, t in batch.schema_snapshot.items()
                    if baseline_schema.get(c) == "unknown" and t != "unknown"
                }
                if learned:
                    st.schema = {**baseline_schema, **learned}
                    return None, True
            return None, False

        added_types = {
            c: (batch.schema_snapshot.get(c) if batch.schema_snapshot else None) for c in added
        }
        if self._schema_intent_covers(st, added_types, removed, {}):
            self._adopt_schema_from_intent(batch, st)
            return None, True

        severity = DriftSeverity.HIGH if removed else DriftSeverity.MEDIUM
        if len(removed) > len(baseline_cols) * 0.5:
            severity = DriftSeverity.CRITICAL

        return {
            "type": "schema_change",
            "added": list(added),
            "removed": list(removed),
            "affected_columns": list(added | removed),
            "severity": severity,
            "details": f"Added: {list(added)}, Removed: {list(removed)}",
        }, False

    def _schema_intent_covers(
        self,
        st: SourceState,
        added: Dict[str, Optional[str]],
        removed: set,
        type_changed: Dict[str, str],
    ) -> bool:
        """True if a live, unexpired declared intent covers this exact diff.

        Coverage is a subset check per change kind -- an undeclared extra
        column change (an addition on top of a declared one, say) falls
        through to the ordinary drift path unchanged, which is the
        guarantee that an intent only ever suppresses the specific change
        it named.

        ``added`` and ``type_changed`` map column -> the batch's *observed*
        new type (``None`` when the batch carried no ``schema_snapshot`` and
        the type genuinely can't be known). Matching by column name alone
        would let a declared intent for e.g. "a: int -> float" silently
        absorb ANY subsequent type on "a" (say a corrupt "a: int -> string")
        for the rest of the TTL window -- so when the observed type is
        known, it must equal the declared target type, not just share the
        column name.
        """
        if not self._schema_intent_enabled:
            return False
        intent = st.schema_intent
        if not intent:
            return False
        if intent.get("expires_at", 0.0) <= time.time():
            return False
        declared_added = dict(intent.get("added") or {})
        declared_removed = set(intent.get("removed") or [])
        declared_type_changes = dict(intent.get("type_changes") or {})

        if not removed <= declared_removed:
            return False
        for col, observed_type in added.items():
            if col not in declared_added:
                return False
            if observed_type is not None and observed_type != declared_added[col]:
                return False
        for col, observed_type in type_changed.items():
            if col not in declared_type_changes:
                return False
            if observed_type is not None and observed_type != declared_type_changes[col]:
                return False
        return True

    @staticmethod
    def _adopt_schema_from_intent(batch: BatchPayload, st: SourceState) -> None:
        """Adopt the batch's schema as the new baseline (intent-covered change).

        Mirrors the first-time-seen assignment in ``_check_schema_drift``.
        Clears the intent once the schema has fully reached the declared
        target -- every declared add present, no declared removal still
        present, AND every declared type change actually landed at its
        target type -- so a multi-batch rollout across app instances (only
        some columns changed this batch) keeps being tolerated until then,
        or until TTL expiry. Checking only added/removed here let a
        multi-column type-change intent get cleared after just the first
        column's type changed, spuriously flagging the still-pending
        declared columns as drift once the intent was already gone.
        """
        if batch.schema_snapshot:
            st.schema = dict(batch.schema_snapshot)
        elif batch.columns:
            st.schema = {c: "unknown" for c in batch.columns}

        intent = st.schema_intent
        if intent is None:
            return
        declared_added = set(intent.get("added") or {})
        declared_removed = set(intent.get("removed") or [])
        declared_type_changes = dict(intent.get("type_changes") or {})
        new_schema = st.schema or {}
        new_cols = set(new_schema.keys())
        target_reached = (
            declared_added <= new_cols
            and not (declared_removed & new_cols)
            and all(
                new_schema.get(col) == target_type
                for col, target_type in declared_type_changes.items()
            )
        )
        if target_reached:
            st.schema_intent = None

    # ────────────────────────────────────────────────────────────────
    # Statistical drift — KL-Divergence
    # ────────────────────────────────────────────────────────────────

    def _check_statistical_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Returns ``(drift_or_None, state_was_mutated)``."""
        baseline = st.baseline
        if not baseline or not batch.rows:
            return None, False

        # BUG-564: bin each numeric column on the BASELINE's edges. Binning a
        # batch on its own min/max made bin i cover different values in the
        # two histograms, so a clean batch of another size (a wider or
        # narrower range) read as drift, and a pure shift of the whole range
        # read as no drift at all.
        bin_edges = {
            col: edges for col, dist in baseline.items()
            if (edges := self._baseline_edges(dist.histogram)) is not None
        }
        batch_dists = self._compute_distributions(batch, bin_edges=bin_edges)
        drifted_cols: List[str] = []
        kl_values: Dict[str, float] = {}
        col_stats: Dict[str, Dict[str, Any]] = {}
        max_kl = 0.0
        max_loc_shift = 0.0
        kind_changed = False

        zeta = self._dynamic_threshold(st.kl_history)

        for col_name, batch_dist in batch_dists.items():
            if col_name not in baseline:
                continue

            ref_dist = baseline[col_name]
            significant = True
            if batch_dist.histogram.get("aligned"):
                kl, significant = self._aligned_kl(
                    batch_dist.histogram["counts"], ref_dist.histogram["counts"])
            else:
                kl = self._kl_divergence(batch_dist.histogram, ref_dist.histogram)
            kind_changed = kind_changed or kl == _KIND_CHANGE_KL

            # Location / scale shift: if both have mean & std, compute a
            # normalised distance so that range shifts (e.g. 10-500 vs
            # 10 000-500 000) are detected even when histogram shapes match.
            if (
                ref_dist.mean is not None
                and batch_dist.mean is not None
                and ref_dist.std is not None
            ):
                # BUG-012 (docs/BUG_REGISTRY.md): a column that is truly constant in
                # real data (e.g. improvement_surcharge, always 0.3) gets a non-zero
                # std from float rounding (numpy.std on N copies of the same float64
                # -> ~1e-17), not the mathematically-true 0. `> 0` only caught the
                # exact-zero case, so this noise floor became the denominator of a
                # "sigma" score -- turning a float-precision residual into an
                # astronomical fake sigma count that then poisons the persisted
                # kl_history the adaptive threshold reads back on every later batch.
                # Floor at _EPS, same epsilon already used for the KL calculation
                # below, so a std indistinguishable from float noise is treated as
                # the exact-zero case already handled.
                scale = ref_dist.std if ref_dist.std > _EPS else 1.0
                loc_shift = abs(batch_dist.mean - ref_dist.mean) / scale
                max_loc_shift = max(max_loc_shift, loc_shift)
                # Treat a >2-sigma shift as additional KL
                if loc_shift > 2.0:
                    kl = max(kl, loc_shift * zeta)
                    significant = True

            kl_values[col_name] = round(kl, 6)

            # Preserve location/scale so the actuator can build a
            # deterministic rescale shim for unit-bug drift.
            col_stats[col_name] = {
                "baseline_mean": ref_dist.mean,
                "batch_mean": batch_dist.mean,
                "baseline_std": ref_dist.std,
            }

            # BUG-564: an aligned-histogram KL past zeta that is not
            # significant for these sample sizes is noise (see _aligned_kl).
            if kl > zeta and significant:
                drifted_cols.append(col_name)
                max_kl = max(max_kl, kl)

        # Record for dynamic threshold adaptation. BUG-417: not when a column's
        # location moved by more than 2 sigma. That batch's score is the surrogate
        # `loc_shift * zeta`, so feeding it back raised zeta in proportion to itself
        # -- one unit-bug detection lifted the threshold above the largest KL a
        # histogram can produce, hiding shape drift for the next 200 batches.
        # A column that changed kind is likewise a fault, not a sample of this
        # source's ordinary noise.
        mutated = False
        if kl_values and max_loc_shift <= 2.0 and not kind_changed:
            avg_kl = sum(kl_values.values()) / len(kl_values)
            st.kl_history.append(avg_kl)
            # Keep last 200 samples
            if len(st.kl_history) > 200:
                del st.kl_history[:-200]
            mutated = True

        if not drifted_cols:
            return None, mutated

        severity = DriftSeverity.LOW
        if max_kl > zeta * 3:
            severity = DriftSeverity.CRITICAL
        elif max_kl > zeta * 2:
            severity = DriftSeverity.HIGH
        elif max_kl > zeta * 1.5:
            severity = DriftSeverity.MEDIUM

        # Cold-start warmup: for the first ``warmup_batches`` numeric batches
        # per source the adaptive-ζ window is not yet full, so ζ is the flat
        # default and a 50-bin histogram estimated off ~500 rows carries
        # Poisson bin-noise large enough to push KL past ζ on a perfectly
        # healthy stream.  These false alarms are distinguishable from real
        # drift by a robust statistic: the healthy noise leaves the column
        # LOCATION unmoved (empirically <0.2σ), whereas a genuine unit bug or
        # regime change shifts the mean by many σ.  During warmup we therefore
        # still RECORD the KL sample (so the window fills) but SUPPRESS the
        # alert unless the location has genuinely moved (>2σ) — trusting the
        # robust location/scale statistic over the noisy per-bin KL while the
        # histogram is under-sampled.  Steady-state drift fires normally once
        # the window fills.  Set warmup_batches=0 to restore original behaviour.
        if (
            self._warmup_batches
            and len(st.kl_history) <= self._warmup_batches
            and max_loc_shift <= 2.0
            and not kind_changed
        ):
            return None, mutated

        return {
            "type": "statistical",
            "affected_columns": drifted_cols,
            "kl_values": kl_values,
            "col_stats": col_stats,
            "max_kl": round(max_kl, 6),
            "threshold_zeta": round(zeta, 6),
            "severity": severity,
            "details": (
                f"KL divergence exceeded ζ={zeta:.4f} in {len(drifted_cols)} column(s). "
                f"Max D_KL={max_kl:.4f}"
            ),
        }, mutated

    # ────────────────────────────────────────────────────────────────
    # Semantic drift — embedding cosine distance
    # ────────────────────────────────────────────────────────────────

    def _check_semantic_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Optional[Dict[str, Any]]:
        refs = st.embeddings
        if not refs:
            return None

        batch_emb = self._compute_batch_embedding(batch)
        if not batch_emb:
            return None

        # Compare against reference context matrix (average distance)
        min_distance = float("inf")
        for ref_emb in refs:
            dist = self._cosine_distance(batch_emb, ref_emb)
            min_distance = min(min_distance, dist)

        if min_distance <= self._semantic_threshold:
            return None

        severity = DriftSeverity.LOW
        if min_distance > self._semantic_threshold * 3:
            severity = DriftSeverity.CRITICAL
        elif min_distance > self._semantic_threshold * 2:
            severity = DriftSeverity.HIGH
        elif min_distance > self._semantic_threshold * 1.5:
            severity = DriftSeverity.MEDIUM

        return {
            "type": "semantic",
            "cosine_distance": round(min_distance, 6),
            "threshold": self._semantic_threshold,
            "severity": severity,
            "details": f"Semantic distance {min_distance:.4f} exceeds threshold {self._semantic_threshold}",
        }

    # ────────────────────────────────────────────────────────────────
    # Data-quality drift — null rate, volume, duplicates (BUG-568)
    # ------------------------------------------------------------------
    # Each threshold is set so sampling noise on clean data cannot reach it
    # (ADR-002: zero false alarms on clean data), at any batch size from a
    # tenth to ten times the baseline's: a finding needs both an effect far
    # beyond the noise of the two sample sizes involved AND a minimum effect
    # size, so a large batch cannot turn a negligible wobble into an alarm.
    # ────────────────────────────────────────────────────────────────

    def _check_null_rate_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Optional[Dict[str, Any]]:
        """A column's null rate ROSE versus the baseline's.

        Two-proportion z-test on add-one-smoothed rates, so a baseline with
        no nulls does not make a single null in a small batch look
        infinitely significant. Fires at z >= 5 (≈3e-7 per column on clean
        data) with an absolute rise of at least 5 points. A column that is
        null in every batch row counts like any other -- that is the
        blanked-column fault this exists to catch.
        """
        baseline = st.baseline
        n1 = len(batch.rows)
        if not baseline or n1 == 0:
            return None
        present = set(batch.columns) if batch.columns else {k for r in batch.rows for k in r}
        rates: Dict[str, Dict[str, float]] = {}
        for col, dist in baseline.items():
            # A column the batch no longer carries is schema drift, not nulls.
            if (
                col not in present or dist.null_rate is None or dist.null_rate >= 1.0
                or not dist.sample_size
            ):
                continue
            n0 = max(int(round(dist.sample_size / (1.0 - dist.null_rate))), dist.sample_size)
            k0 = n0 - dist.sample_size
            k1 = sum(1 for r in batch.rows if r.get(col) is None)
            p0 = (k0 + 1) / (n0 + 2)
            p1 = (k1 + 1) / (n1 + 2)
            se = math.sqrt(p0 * (1 - p0) / (n0 + 2) + p1 * (1 - p1) / (n1 + 2))
            rise = k1 / n1 - k0 / n0
            if rise >= _NULL_MIN_RISE and (p1 - p0) / se >= _DQ_Z:
                rates[col] = {
                    "baseline_null_rate": round(k0 / n0, 6),
                    "batch_null_rate": round(k1 / n1, 6),
                }
        if not rates:
            return None
        max_rise = max(r["batch_null_rate"] - r["baseline_null_rate"] for r in rates.values())
        return {
            "type": "null_rate",
            "affected_columns": list(rates),
            "null_rates": rates,
            "severity": self._fraction_severity(max_rise),
            "details": "Null rate rose in column(s): " + ", ".join(
                f"{c} {r['baseline_null_rate']:.1%} -> {r['batch_null_rate']:.1%}"
                for c, r in rates.items()
            ),
        }

    def _check_volume_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Optional[Dict[str, Any]]:
        """Row count of a whole run more than 100x below or above the baseline run's.

        BUG-568 review: volume is a property of a RUN (or period), never of a
        micro-batch. Real callers only cap batch size -- the connections
        ingest bridge pages a table in fixed batch_size chunks, so its last
        page is short; a quiet Kafka poll window yields a handful of rows --
        so comparing a micro-batch with the baseline batch alarmed on clean
        data. The check therefore runs only when BOTH the batch and the
        baseline declare themselves a complete run
        (``metadata["complete_run"] is True``). Anything undeclared,
        including a distributions-only baseline whose run size is unknown,
        is never judged: no guessing (ADR-002).
        """
        if not _is_complete_run(batch):
            return None
        profile = st.baseline_rows
        if not profile or not profile.get("complete_run"):
            return None
        n0 = int(profile["rows"])
        if n0 < _VOLUME_MIN_BASELINE_ROWS:
            return None
        n1 = len(batch.rows)
        if n0 <= n1 * _VOLUME_FACTOR and n1 <= n0 * _VOLUME_FACTOR:
            return None
        return {
            "type": "volume",
            "affected_columns": [],
            "baseline_rows": n0,
            "batch_rows": n1,
            "severity": DriftSeverity.CRITICAL if n1 == 0 else DriftSeverity.HIGH,
            "details": f"Run row count {n1} vs baseline run {n0} (more than {_VOLUME_FACTOR}x apart)",
        }

    @staticmethod
    def _equal_row_pairs(batch: BatchPayload, columns: Optional[List[str]] = None) -> int:
        """Number of row pairs in the batch that are exact duplicates."""
        cols = columns or batch.columns or (list(batch.rows[0].keys()) if batch.rows else [])
        counts = Counter(repr(tuple(r.get(c) for c in cols)) for r in batch.rows)
        return sum(c * (c - 1) // 2 for c in counts.values())

    def _check_duplicate_drift(
        self, batch: BatchPayload, st: SourceState
    ) -> Optional[Dict[str, Any]]:
        """More exact-duplicate rows than the baseline's own repeat rate explains.

        Comparing duplicate RATES directly would be wrong: data with a
        finite set of possible rows repeats more the more rows you draw, so a
        clean 10x batch has a far higher duplicate rate than its baseline.
        The count of equal row pairs scales exactly with n(n-1)/2 for any
        row distribution, so the baseline's pair count -- raised to a
        Poisson upper bound, (sqrt(k+1)+3)^2, so a lucky low baseline cannot
        set the bar -- is scaled to the batch's size. The batch must exceed
        twice that, plus 10 pairs.
        """
        profile = st.baseline_rows
        if not profile or not profile.get("columns"):
            return None
        cols = list(profile["columns"])
        present = set(batch.columns) if batch.columns else {k for r in batch.rows for k in r}
        if not set(cols) <= present:
            return None  # a removed column makes rows less unique: schema drift's job
        n0, k0 = int(profile["rows"]), int(profile["equal_pairs"])
        n1 = len(batch.rows)
        if n0 < 2 or n1 < 2:
            return None
        k0_upper = (math.sqrt(k0 + 1) + 3) ** 2
        expected_upper = k0_upper * (n1 * (n1 - 1)) / (n0 * (n0 - 1))
        k1 = self._equal_row_pairs(batch, cols)
        if k1 <= 2 * expected_upper + 10:
            return None
        distinct = len({repr(tuple(r.get(c) for c in cols)) for r in batch.rows})
        dup_fraction = (n1 - distinct) / n1
        return {
            "type": "duplicate",
            "affected_columns": [],
            "equal_pairs": k1,
            "expected_pairs_upper": round(expected_upper, 3),
            "duplicate_rows": n1 - distinct,
            "duplicate_fraction": round(dup_fraction, 6),
            "severity": self._fraction_severity(dup_fraction),
            "details": (
                f"{n1 - distinct} duplicate row(s) ({dup_fraction:.1%}); {k1} equal pairs "
                f"vs at most {expected_upper:.1f} expected from the baseline"
            ),
        }

    @staticmethod
    def _fraction_severity(fraction: float) -> DriftSeverity:
        if fraction >= 0.5:
            return DriftSeverity.CRITICAL
        if fraction >= 0.25:
            return DriftSeverity.HIGH
        if fraction >= 0.1:
            return DriftSeverity.MEDIUM
        return DriftSeverity.LOW

    # ────────────────────────────────────────────────────────────────
    # KL-Divergence computation
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _kl_divergence(p_hist: Dict[str, Any], q_hist: Dict[str, Any]) -> float:
        """
        Compute KL(P || Q) from two histogram dicts.

        Histogram format: {"bins": [...], "counts": [...]}
        For categorical: {"categories": {"a": 10, "b": 5}}
        Plain dicts like {"a": 0.9, "b": 0.1} are also accepted as
        categorical distributions (keys → categories, values → weights).
        """
        # Detect plain categorical dict (no special keys)
        _special = {"categories", "bins", "counts"}
        p_is_plain = bool(p_hist) and not (set(p_hist.keys()) & _special)
        q_is_plain = bool(q_hist) and not (set(q_hist.keys()) & _special)

        # Normalise to {"categories": {...}} form
        if p_is_plain:
            p_hist = {"categories": p_hist}
        if q_is_plain:
            q_hist = {"categories": q_hist}

        # Categorical histograms
        if "categories" in p_hist and "categories" in q_hist:
            p_cats = p_hist["categories"]
            q_cats = q_hist["categories"]
            all_keys = set(p_cats.keys()) | set(q_cats.keys())

            p_total = sum(p_cats.values()) or 1
            q_total = sum(q_cats.values()) or 1

            kl = 0.0
            for k in all_keys:
                p_val = (p_cats.get(k, 0) / p_total) + _EPS
                q_val = (q_cats.get(k, 0) / q_total) + _EPS
                kl += p_val * math.log(p_val / q_val)
            return max(kl, 0.0)

        # Numeric histograms with bins/counts
        p_counts = p_hist.get("counts", [])
        q_counts = q_hist.get("counts", [])

        # BUG-418: one side numeric, the other categorical -- the column changed
        # kind (a numeric column now carries text, or the reverse). The two share no
        # support, so this is the largest divergence the epsilon floor allows; it used
        # to fall through to the empty-counts case below and score 0.
        if (p_counts and "categories" in q_hist) or (q_counts and "categories" in p_hist):
            return _KIND_CHANGE_KL

        if not p_counts or not q_counts:
            return 0.0

        # Align lengths
        max_len = max(len(p_counts), len(q_counts))
        p_arr = list(p_counts) + [0] * (max_len - len(p_counts))
        q_arr = list(q_counts) + [0] * (max_len - len(q_counts))

        p_total = sum(p_arr) or 1
        q_total = sum(q_arr) or 1

        kl = 0.0
        for i in range(max_len):
            p_val = (p_arr[i] / p_total) + _EPS
            q_val = (q_arr[i] / q_total) + _EPS
            kl += p_val * math.log(p_val / q_val)

        return max(kl, 0.0)

    # ────────────────────────────────────────────────────────────────
    # Dynamic threshold
    # ────────────────────────────────────────────────────────────────

    def _dynamic_threshold(self, history: List[float]) -> float:
        """
        Adaptive ζ based on historical KL variance.

        ζ = mean(D_KL) + 2·std(D_KL), floored at HALF the default (not the
        full default -- BUG-071: this docstring previously said "floored at
        the default", but the `* 0.5` below has been the actual, long-
        standing implementation since this method's introduction, with
        existing tests asserting the resulting sensitivity. Since the
        behavior is what production has always run and is exercised by
        tests, this fix corrects the doc to match the code rather than
        changing live detection thresholds).

        Once a source has ≥5 KL samples, this lets ζ settle as low as
        `default_zeta / 2` for a genuinely low-noise source -- up to 2x
        more sensitive than the flat default before enough history exists.
        """
        if len(history) < 5:
            return self._default_zeta

        mean_kl = sum(history) / len(history)
        var_kl = sum((x - mean_kl) ** 2 for x in history) / len(history)
        std_kl = math.sqrt(var_kl)

        adaptive = mean_kl + 2 * std_kl
        return max(adaptive, self._default_zeta * 0.5)

    # ────────────────────────────────────────────────────────────────
    # Distribution computation from raw batch
    # ────────────────────────────────────────────────────────────────

    def _compute_distributions(
        self,
        batch: BatchPayload,
        bin_edges: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, ColumnDistribution]:
        """Build column-level distributions from batch rows.

        ``bin_edges`` maps a column to a baseline's histogram edges; a numeric
        column listed there is binned on those edges (see
        ``_histogram_on_edges``) instead of its own range.
        """
        if not batch.rows:
            return {}

        columns = batch.columns or (list(batch.rows[0].keys()) if batch.rows else [])
        result: Dict[str, ColumnDistribution] = {}

        for col in columns:
            values = [row.get(col) for row in batch.rows if row.get(col) is not None]
            if not values:
                continue

            dist = ColumnDistribution(column_name=col, sample_size=len(values))

            # Numeric vs categorical: attempt a single vectorized float cast.
            # np.asarray(..., dtype=float) raises if ANY value is non-numeric,
            # preserving the previous all-or-nothing type detection while
            # replacing three O(n) Python passes (per-element float(),
            # sum-of-squares, set()) with GIL-releasing numpy kernels.
            numeric_arr = None
            try:
                numeric_arr = np.asarray(values, dtype=float)
            except (ValueError, TypeError):
                numeric_arr = None

            if numeric_arr is not None and numeric_arr.size:
                # Numeric distribution (population std, ddof=0 — matches prior)
                dist.mean = float(numeric_arr.mean())
                dist.std = float(numeric_arr.std())
                dist.null_rate = 1.0 - len(values) / max(len(batch.rows), 1)
                dist.distinct_count = int(np.unique(numeric_arr).size)
                if bin_edges and col in bin_edges:
                    dist.histogram = self._histogram_on_edges(numeric_arr, bin_edges[col])
                else:
                    dist.histogram = self._build_numeric_histogram(numeric_arr)
            else:
                # Categorical distribution
                str_vals = [str(v) for v in values]
                counts = Counter(str_vals)
                dist.distinct_count = len(counts)
                dist.null_rate = 1.0 - len(values) / max(len(batch.rows), 1)
                dist.histogram = {"categories": dict(counts)}

            result[col] = dist

        return result

    @staticmethod
    def _build_numeric_histogram(values, bins: int = _DEFAULT_BINS) -> Dict[str, Any]:
        """Build a simple equal-width histogram.

        Accepts a list or a numpy array. Mirrors the previous binning
        ``min(int((v - min) / width), bins - 1)`` exactly via np.bincount,
        so counts are bit-for-bit identical to the pure-Python loop while
        avoiding a per-element Python pass.
        """
        arr = np.asarray(values, dtype=float)
        if arr.size == 0:
            return {"bins": [], "counts": []}

        min_val = float(arr.min())
        max_val = float(arr.max())

        if min_val == max_val:
            return {"bins": [min_val], "counts": [int(arr.size)]}

        bin_width = (max_val - min_val) / bins
        bin_edges = [min_val + i * bin_width for i in range(bins + 1)]
        idx = np.minimum(((arr - min_val) / bin_width).astype(np.int64), bins - 1)
        counts = np.bincount(idx, minlength=bins)[:bins].astype(int).tolist()

        return {"bins": bin_edges, "counts": counts}

    @staticmethod
    def _baseline_edges(hist: Dict[str, Any]) -> Optional[List[float]]:
        """The edges of a numeric histogram built by ``_build_numeric_histogram``.

        ``bins`` holds len(counts)+1 edges, or a single value for a constant
        column. A histogram of any other shape (a caller-supplied dict) has
        no usable edges and keeps the legacy index-wise comparison.
        """
        edges = hist.get("bins")
        counts = hist.get("counts")
        if not edges or not counts:
            return None
        if len(edges) == len(counts) + 1 or (len(edges) == 1 and len(counts) == 1):
            return [float(e) for e in edges]
        return None

    @staticmethod
    def _histogram_on_edges(values, edges: List[float]) -> Dict[str, Any]:
        """Counts of ``values`` on a baseline's edges, plus an underflow and an
        overflow bin (BUG-564).

        Interior bins use the same ``min(int((v - lo) / width), bins - 1)``
        rule that built the baseline, so the baseline's own rows re-bin to its
        own counts. Values outside the baseline's range land in the end bins
        instead of stretching the bins, which is what lets a shift or a
        widening of the range show up as divergence. A constant baseline
        (one edge) gets three bins: below, equal to, above that value.
        """
        arr = np.asarray(values, dtype=float)
        if len(edges) == 1:
            v = edges[0]
            counts = [int((arr < v).sum()), int((arr == v).sum()), int((arr > v).sum())]
            return {"bins": edges, "counts": counts, "aligned": True}

        lo, hi = edges[0], edges[-1]
        nbins = len(edges) - 1
        width = (hi - lo) / nbins
        # The stored top edge is lo + nbins * width in floating point, which
        # can land a few ulps below the baseline's true max; tolerate that so
        # the baseline's own max value is not counted as overflow.
        hi_tol = hi + 4 * float(np.spacing(abs(hi)))
        under = arr < lo
        over = arr > hi_tol
        inside = arr[~(under | over)]
        idx = np.clip(((inside - lo) / width).astype(np.int64), 0, nbins - 1)
        inner = np.bincount(idx, minlength=nbins)[:nbins].astype(int).tolist()
        counts = [int(under.sum()), *inner, int(over.sum())]
        return {"bins": edges, "counts": counts, "aligned": True}

    @staticmethod
    def _aligned_kl(p_counts: List[int], q_inner_counts: List[int]) -> Tuple[float, bool]:
        """KL(P || Q) for a batch binned by ``_histogram_on_edges`` against the
        baseline counts it was binned on (BUG-564), and whether that
        divergence is statistically significant.

        Three finite-sample corrections keep a clean batch of ANY size from
        firing:

        * Adjacent baseline bins are merged so each holds about
          ``_MIN_ROWS_PER_BIN`` rows of the smaller of the two samples. The
          plug-in KL of an n-row sample over k bins is biased upward by about
          (k-1)/2n even when nothing changed -- ~0.25 for 100 rows over 50
          bins, past the default zeta of 0.15 on its own.
        * The baseline gets empty under/overflow bins and a 0.5 pseudo-count
          in every bin. A bin the baseline happened to leave empty (a sparse
          tail, the range just past its max) does not have probability zero;
          with the bare 1e-10 floor used elsewhere, ONE clean value there cost
          (1/n)·ln(1/(n·1e-10)) -- ≈0.18 at n=100.
        * Significance: 2·n_eff·KL (the G statistic, n_eff = n0·n1/(n0+n1))
          is ~chi-square(bins-1) when nothing changed. A KL above zeta from a
          20-row batch is mostly noise, so the caller only lets the KL fire
          when G is past the chi-square 1e-6 upper quantile.

        Real change still diverges strongly: a 1-sd shift of a normal column
        scores ≈0.3 or more, and mass past the baseline's range far more.
        """
        p = np.asarray(p_counts, dtype=float)
        q_inner = np.asarray(q_inner_counts, dtype=float)
        if len(p) != len(q_inner) + 2:
            return 0.0, False
        n1, n0 = p.sum(), q_inner.sum()
        if n1 <= 0 or n0 <= 0:
            return 0.0, False
        nbins = len(q_inner)
        groups = int(min(nbins, max(1, min(n0, n1) // _MIN_ROWS_PER_BIN)))
        width = math.ceil(nbins / groups)
        starts = np.arange(0, nbins, width)
        p = np.concatenate(([p[0]], np.add.reduceat(p[1:-1], starts), [p[-1]]))
        q_raw = np.concatenate(([0.0], np.add.reduceat(q_inner, starts), [0.0]))
        df = max(int(np.count_nonzero((p > 0) | (q_raw > 0))) - 1, 1)
        q = (q_raw + 0.5) / (q_raw.sum() + 0.5 * len(q_raw))
        p = p / n1
        nz = p > 0
        kl = max(float(np.sum(p[nz] * np.log(p[nz] / q[nz]))), 0.0)
        g_stat = 2.0 * kl * (n0 * n1 / (n0 + n1))
        # Wilson-Hilferty chi-square quantile; z = 4.753 is the 1e-6 upper
        # tail of the normal. It overstates the quantile at df=1 (27.5 vs
        # 23.9), which errs toward not firing.
        z = 4.753
        crit = df * (1 - 2 / (9 * df) + z * math.sqrt(2 / (9 * df))) ** 3
        return kl, bool(g_stat > crit)

    # ────────────────────────────────────────────────────────────────
    # Batch embedding (hash-projection, no external API needed)
    # ────────────────────────────────────────────────────────────────

    def _categorical_columns(self, batch: BatchPayload) -> List[str]:
        """Return columns whose values are *not* fully numeric.

        A column counts as categorical/textual when at least one non-null
        value fails to parse as ``float``.  Fully-numeric columns are
        excluded from the semantic embedding (see ``_compute_batch_embedding``)
        because hashing ``col:value`` for continuous data yields a unique
        token per row.  This mirrors the numeric-vs-categorical split used by
        ``_compute_distributions`` so the two channels agree on column type.
        """
        if not batch.rows:
            return []
        columns = batch.columns or list(batch.rows[0].keys())
        categorical: List[str] = []
        for col in columns:
            saw_value = False
            is_numeric = True
            for row in batch.rows:
                val = row.get(col)
                if val is None:
                    continue
                saw_value = True
                try:
                    float(val)
                except (ValueError, TypeError):
                    is_numeric = False
                    break
            if saw_value and not is_numeric:
                categorical.append(col)
        return categorical

    def _compute_batch_embedding(self, batch: BatchPayload, dim: int = 256) -> Optional[List[float]]:
        """
        Compute a lightweight embedding for the entire batch using
        feature hashing (random projection). No external model needed.

        Only *categorical / textual* columns contribute to the embedding.
        Continuous numeric columns are excluded: hashing ``col:value`` for
        floating-point data yields a unique token per row, so any two batches
        share almost no dimensions and the cosine distance is ~1.0 regardless
        of whether the underlying distribution actually drifted.  Feeding such
        columns to the semantic channel produced constant false-positive
        SEMANTIC/CRITICAL alarms on healthy numeric streams.  When a batch has
        no categorical columns this returns ``None`` and the caller skips the
        semantic channel entirely.
        """
        if not batch.rows:
            return None

        categorical_cols = self._categorical_columns(batch)
        if not categorical_cols:
            return None

        vector = [0.0] * dim
        row_count = 0

        for row in batch.rows:
            for col in categorical_cols:
                val = row.get(col)
                if val is None:
                    continue
                # Hash column+value to get a dimension index. BUG-069: must be
                # a STABLE hash -- Python's builtin hash() is randomized per
                # process (PEP 456 / PYTHONHASHSEED) unless explicitly
                # disabled, so the same token maps to a different dimension
                # in every process. A baseline embedding registered by one
                # process (persisted via RedisStateStore, explicitly meant to
                # be shared across worker replicas / survive a restart) then
                # shares almost no dimensions with a batch embedded by a
                # different process, making cosine distance ~1.0 regardless
                # of whether the data actually drifted -- silently defeating
                # the semantic-drift channel. zlib.crc32 is deterministic
                # across processes/restarts. It's unsigned, so the sign bit
                # (needed for the random-projection/SimHash trick) is derived
                # from a low bit of the same hash rather than from crc32's
                # sign (which doesn't exist), and the index uses the
                # remaining bits so the two don't reuse the same bit.
                token = f"{col}:{val}"
                h = zlib.crc32(token.encode("utf-8"))
                idx = (h >> 1) % dim
                sign = 1.0 if (h & 1) else -1.0
                vector[idx] += sign
                row_count += 1

        # Normalize
        if row_count > 0:
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            vector = [x / norm for x in vector]
        else:
            return None

        return vector

    # ────────────────────────────────────────────────────────────────
    # Cosine distance
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _cosine_distance(a: List[float], b: List[float]) -> float:
        """1 - cosine_similarity(a, b)."""
        if len(a) != len(b):
            return 1.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
        norm_b = math.sqrt(sum(x * x for x in b)) or 1.0

        similarity = dot / (norm_a * norm_b)
        return 1.0 - similarity

    # ────────────────────────────────────────────────────────────────
    # Utility — snapshot current distributions for persistence
    # ────────────────────────────────────────────────────────────────

    def snapshot_distributions(
        self, batch: BatchPayload
    ) -> Dict[str, ColumnDistribution]:
        """Compute & return distributions without checking drift. Useful for initial baselining."""
        return self._compute_distributions(batch)
