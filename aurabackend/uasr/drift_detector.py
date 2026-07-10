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
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .models import (
    BatchPayload,
    ColumnDistribution,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
)
from .state_store import InMemoryStateStore, SourceState, StateStore

logger = logging.getLogger("uasr.drift_detector")

# Small constant to prevent log(0)
_EPS = 1e-10

# Default bins for continuous-valued histograms
_DEFAULT_BINS = 50


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
    ) -> None:
        # ζ — KL-divergence threshold.  Adapted dynamically per source.
        self._default_zeta = default_zeta
        self._schema_strict = schema_strict
        self._semantic_threshold = semantic_threshold
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
        else:
            distributions = distributions_or_batch

        st = self._store.load(source_id)
        st.baseline = distributions
        if schema:
            st.schema = schema
        self._store.save(source_id, st)
        logger.info("Registered baseline for source=%s (%d columns)", source_id, len(distributions))

    def register_reference_embedding(self, source_id: str, embedding: List[float]) -> None:
        """Add a reference embedding vector to the source's context matrix."""
        st = self._store.load(source_id)
        st.embeddings.append(embedding)
        self._store.save(source_id, st)

    # ────────────────────────────────────────────────────────────────
    # Main detection
    # ────────────────────────────────────────────────────────────────

    def detect(self, batch: BatchPayload) -> DriftDetectionResult:
        """Run all drift checks on an incoming batch."""
        result = DriftDetectionResult(
            source_id=batch.source_id,
            batch_id=batch.batch_id,
        )

        # Load this source's state ONCE per batch (single store round-trip,
        # single LRU touch), thread it through the checks, and persist ONCE
        # at the end.  ``dirty`` tracks whether any check mutated the state
        # (first-seen schema baseline, appended KL sample) so we avoid a
        # redundant write on read-only batches.
        st = self._store.load(batch.source_id)
        dirty = False

        # 1. Schema drift
        schema_drift, d1 = self._check_schema_drift(batch, st)
        dirty = dirty or d1
        if schema_drift:
            result.drift_detected = True
            result.drift_type = DriftType.SCHEMA
            result.severity = schema_drift["severity"]
            result.affected_columns = schema_drift["affected_columns"]
            result.drift_vector = schema_drift
            result.details = schema_drift.get("details", "")
            if dirty:
                self._store.save(batch.source_id, st)
            return result

        # 2. Statistical drift (KL-Divergence)
        stat_drift, d2 = self._check_statistical_drift(batch, st)
        dirty = dirty or d2
        if stat_drift:
            result.drift_detected = True
            result.drift_type = DriftType.STATISTICAL
            result.severity = stat_drift["severity"]
            result.kl_divergence = stat_drift["max_kl"]
            result.affected_columns = stat_drift["affected_columns"]
            result.drift_vector = stat_drift
            result.details = stat_drift.get("details", "")
            if dirty:
                self._store.save(batch.source_id, st)
            return result

        # 3. Semantic drift (embedding distance) — only if embeddings registered
        sem_drift = self._check_semantic_drift(batch, st)
        if sem_drift:
            result.drift_detected = True
            result.drift_type = DriftType.SEMANTIC
            result.severity = sem_drift["severity"]
            result.cosine_distance = sem_drift["cosine_distance"]
            result.drift_vector = sem_drift
            result.details = sem_drift.get("details", "")
            if dirty:
                self._store.save(batch.source_id, st)
            return result

        if dirty:
            self._store.save(batch.source_id, st)
        result.details = "No drift detected"
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
                    if col in baseline_schema and baseline_schema[col] != dtype and baseline_schema[col] != "unknown":
                        type_changes.append(col)
                if type_changes:
                    return {
                        "type": "type_change",
                        "affected_columns": type_changes,
                        "severity": DriftSeverity.MEDIUM,
                        "details": f"Type changed in columns: {type_changes}",
                        "old_types": {c: baseline_schema[c] for c in type_changes},
                        "new_types": {c: batch.schema_snapshot[c] for c in type_changes},
                    }, False
            return None, False

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

        batch_dists = self._compute_distributions(batch)
        drifted_cols: List[str] = []
        kl_values: Dict[str, float] = {}
        col_stats: Dict[str, Dict[str, Any]] = {}
        max_kl = 0.0
        max_loc_shift = 0.0

        zeta = self._dynamic_threshold(st.kl_history)

        for col_name, batch_dist in batch_dists.items():
            if col_name not in baseline:
                continue

            ref_dist = baseline[col_name]
            kl = self._kl_divergence(batch_dist.histogram, ref_dist.histogram)

            # Location / scale shift: if both have mean & std, compute a
            # normalised distance so that range shifts (e.g. 10-500 vs
            # 10 000-500 000) are detected even when histogram shapes match.
            if (
                ref_dist.mean is not None
                and batch_dist.mean is not None
                and ref_dist.std is not None
            ):
                scale = ref_dist.std if ref_dist.std > 0 else 1.0
                loc_shift = abs(batch_dist.mean - ref_dist.mean) / scale
                max_loc_shift = max(max_loc_shift, loc_shift)
                # Treat a >2-sigma shift as additional KL
                if loc_shift > 2.0:
                    kl = max(kl, loc_shift * zeta)

            kl_values[col_name] = round(kl, 6)

            # Preserve location/scale so the actuator can build a
            # deterministic rescale shim for unit-bug drift.
            col_stats[col_name] = {
                "baseline_mean": ref_dist.mean,
                "batch_mean": batch_dist.mean,
                "baseline_std": ref_dist.std,
            }

            if kl > zeta:
                drifted_cols.append(col_name)
                max_kl = max(max_kl, kl)

        # Record for dynamic threshold adaptation
        mutated = False
        if kl_values:
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
        ζ = mean(D_KL) + 2·std(D_KL), floored at the default.
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

    def _compute_distributions(self, batch: BatchPayload) -> Dict[str, ColumnDistribution]:
        """Build column-level distributions from batch rows."""
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
                # Hash column+value to get a dimension index
                token = f"{col}:{val}"
                h = hash(token)
                idx = abs(h) % dim
                sign = 1.0 if h >= 0 else -1.0
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
