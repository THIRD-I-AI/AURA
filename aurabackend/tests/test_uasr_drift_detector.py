"""
Sprint S32 — UASR DriftDetector + model tests.

Tier A (pure Python, no optional deps).

Covers:
  * Pydantic models: BatchPayload, ColumnDistribution, DriftDetectionResult,
    DiagnosisResult, ShimResult, RecoveryLoopResult
  * Enums: DriftType, DriftSeverity, RecoveryStatus
  * DriftDetector._kl_divergence (categorical + numeric histograms)
  * DriftDetector._cosine_distance
  * DriftDetector._build_numeric_histogram
  * DriftDetector.detect: no-baseline pass-through, schema drift
    (added/removed columns, type change), statistical drift (KL > ζ)
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.drift_detector import DriftDetector
from uasr.models import (
    BatchPayload,
    ColumnDistribution,
    DiagnosisResult,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    RecoveryLoopResult,
    RecoveryStatus,
    ShimResult,
)

# ── Enum tests ────────────────────────────────────────────────────

class TestEnums:
    def test_drift_type_values(self):
        assert DriftType.SCHEMA.value == "schema"
        assert DriftType.STATISTICAL.value == "statistical"
        assert DriftType.SEMANTIC.value == "semantic"
        assert DriftType.MISSING.value == "missing"

    def test_drift_severity_values(self):
        assert DriftSeverity.LOW.value == "low"
        assert DriftSeverity.CRITICAL.value == "critical"

    def test_recovery_status_values(self):
        assert RecoveryStatus.DETECTED.value == "detected"
        assert RecoveryStatus.DEPLOYED.value == "deployed"
        assert RecoveryStatus.ROLLED_BACK.value == "rolled_back"


# ── Pydantic model tests ─────────────────────────────────────────

class TestBatchPayload:
    def test_defaults(self):
        bp = BatchPayload(source_id="src1")
        assert bp.columns == []
        assert bp.rows == []
        assert bp.schema_snapshot is None
        assert bp.metadata == {}
        assert len(bp.batch_id) == 12

    def test_with_data(self):
        bp = BatchPayload(
            source_id="src1", batch_id="b1",
            columns=["a", "b"],
            rows=[{"a": 1, "b": 2}],
        )
        assert bp.batch_id == "b1"
        assert len(bp.rows) == 1


class TestColumnDistribution:
    def test_defaults(self):
        cd = ColumnDistribution(column_name="revenue")
        assert cd.histogram == {}
        assert cd.mean is None
        assert cd.std is None
        assert cd.sample_size is None


class TestDriftDetectionResult:
    def test_no_drift_defaults(self):
        r = DriftDetectionResult(source_id="s1", batch_id="b1")
        assert r.drift_detected is False
        assert r.drift_type is None
        assert r.affected_columns == []


class TestDiagnosisResult:
    def test_defaults(self):
        d = DiagnosisResult(drift_event_id="e1")
        assert d.root_cause == ""
        assert d.confidence == 0.0


class TestShimResult:
    def test_defaults(self):
        s = ShimResult(recovery_id="r1")
        assert s.validation_passed is False
        assert s.deployed is False


class TestRecoveryLoopResult:
    def test_defaults(self):
        r = RecoveryLoopResult(
            drift_event_id="e1", recovery_id="r1",
            status=RecoveryStatus.DETECTED,
        )
        assert r.total_latency_seconds == 0.0
        assert r.evaluation_artifact is None


# ── DriftDetector._kl_divergence ──────────────────────────────────

class TestKLDivergence:
    def test_identical_categorical(self):
        hist = {"categories": {"a": 50, "b": 50}}
        kl = DriftDetector._kl_divergence(hist, hist)
        assert kl < 0.001

    def test_different_categorical(self):
        p = {"categories": {"a": 90, "b": 10}}
        q = {"categories": {"a": 10, "b": 90}}
        kl = DriftDetector._kl_divergence(p, q)
        assert kl > 0.1

    def test_plain_dict_categorical(self):
        p = {"a": 0.9, "b": 0.1}
        q = {"a": 0.5, "b": 0.5}
        kl = DriftDetector._kl_divergence(p, q)
        assert kl > 0.0

    def test_identical_numeric(self):
        hist = {"bins": [0, 5, 10], "counts": [50, 50]}
        kl = DriftDetector._kl_divergence(hist, hist)
        assert kl < 0.001

    def test_different_numeric(self):
        p = {"bins": [0, 5, 10], "counts": [100, 0]}
        q = {"bins": [0, 5, 10], "counts": [0, 100]}
        kl = DriftDetector._kl_divergence(p, q)
        assert kl > 0.1

    def test_empty_counts(self):
        p = {"bins": [], "counts": []}
        q = {"bins": [], "counts": []}
        assert DriftDetector._kl_divergence(p, q) == 0.0

    def test_kl_non_negative(self):
        p = {"categories": {"x": 30, "y": 70}}
        q = {"categories": {"x": 50, "y": 50}}
        assert DriftDetector._kl_divergence(p, q) >= 0.0


# ── DriftDetector._cosine_distance ────────────────────────────────

class TestCosineDistance:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert abs(DriftDetector._cosine_distance(v, v)) < 1e-10

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(DriftDetector._cosine_distance(a, b) - 1.0) < 1e-10

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(DriftDetector._cosine_distance(a, b) - 2.0) < 1e-10

    def test_different_lengths(self):
        assert DriftDetector._cosine_distance([1.0], [1.0, 2.0]) == 1.0


# ── DriftDetector._build_numeric_histogram ────────────────────────

class TestBuildHistogram:
    def test_uniform_values(self):
        vals = [float(i) for i in range(100)]
        hist = DriftDetector._build_numeric_histogram(vals, bins=10)
        assert len(hist["counts"]) == 10
        assert sum(hist["counts"]) == 100
        assert len(hist["bins"]) == 11

    def test_constant_values(self):
        hist = DriftDetector._build_numeric_histogram([5.0] * 50, bins=10)
        assert hist["bins"] == [5.0]
        assert hist["counts"] == [50]

    def test_empty_values(self):
        hist = DriftDetector._build_numeric_histogram([], bins=10)
        assert hist == {"bins": [], "counts": []}


# ── DriftDetector.detect — integration ────────────────────────────

class TestDetectIntegration:
    def _make_batch(self, source_id, columns, rows, schema=None):
        return BatchPayload(
            source_id=source_id,
            columns=columns,
            rows=rows,
            schema_snapshot=schema,
        )

    def test_no_drift_on_identical_data(self):
        det = DriftDetector()
        rows = [{"a": i, "b": i * 2} for i in range(50)]
        baseline = self._make_batch("src", ["a", "b"], rows)
        det.register_baseline("src", baseline)
        result = det.detect(baseline)
        assert result.drift_detected is False

    def test_schema_drift_column_added(self):
        det = DriftDetector()
        baseline = self._make_batch("src", ["a", "b"], [{"a": 1, "b": 2}])
        det.register_baseline("src", baseline)
        new_batch = self._make_batch(
            "src", ["a", "b", "c"],
            [{"a": 1, "b": 2, "c": 3}],
            schema={"a": "int", "b": "int", "c": "int"},
        )
        result = det.detect(new_batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA

    def test_schema_drift_column_removed(self):
        det = DriftDetector()
        baseline = self._make_batch("src", ["a", "b", "c"], [{"a": 1, "b": 2, "c": 3}])
        det.register_baseline("src", baseline)
        new_batch = self._make_batch(
            "src", ["a"],
            [{"a": 1}],
            schema={"a": "int"},
        )
        result = det.detect(new_batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA
        assert result.severity in (DriftSeverity.HIGH, DriftSeverity.CRITICAL)

    def test_statistical_drift_high_kl(self):
        det = DriftDetector(default_zeta=0.05)
        baseline_rows = [{"val": float(i)} for i in range(100)]
        baseline = self._make_batch("src", ["val"], baseline_rows)
        det.register_baseline("src", baseline)
        drifted_rows = [{"val": float(i + 1000)} for i in range(100)]
        drifted = self._make_batch("src", ["val"], drifted_rows)
        result = det.detect(drifted)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.STATISTICAL

    def test_no_baseline_no_crash(self):
        det = DriftDetector()
        batch = self._make_batch("unknown", ["x"], [{"x": 1}])
        result = det.detect(batch)
        assert result.drift_detected is False


# ── Regression: numeric-semantic false alarm (fix 3a) ─────────────

class TestSemanticChannelColumnTyping:
    """Fix 3a — the batch embedding must ignore continuous numeric columns.

    Hashing ``col:value`` for floating-point data yields a unique token per
    row, so any two numeric batches share almost no dimensions and the cosine
    distance is ~1.0.  Before the fix this produced constant false-positive
    SEMANTIC/CRITICAL drift on healthy numeric streams.
    """

    def _numeric_batch(self, source_id, batch_id, n=200, loc=10.0, scale=2.0):
        import random
        rnd = random.Random(f"{source_id}:{batch_id}")
        return BatchPayload(
            source_id=source_id,
            batch_id=batch_id,
            rows=[{"value": rnd.gauss(loc, scale)} for _ in range(n)],
        )

    def _categorical_batch(self, source_id, batch_id, dominant, n=200):
        import random
        rnd = random.Random(f"{source_id}:{batch_id}:{dominant}")
        rows = []
        for _ in range(n):
            r = rnd.random()
            cat = dominant if r < 0.8 else ("B" if r < 0.9 else "C")
            rows.append({"cat": cat})
        return BatchPayload(source_id=source_id, batch_id=batch_id, rows=rows)

    def test_numeric_batch_yields_no_embedding(self):
        det = DriftDetector()
        emb = det._compute_batch_embedding(self._numeric_batch("num", "b0"))
        assert emb is None

    def test_numeric_stream_no_semantic_false_alarm(self):
        det = DriftDetector()
        det.register_baseline("num", self._numeric_batch("num", "base"))
        semantic_fires = 0
        for i in range(50):
            res = det.detect(self._numeric_batch("num", f"b{i}"))
            if res.drift_detected and res.drift_type == DriftType.SEMANTIC:
                semantic_fires += 1
        assert semantic_fires == 0

    def test_categorical_batch_yields_embedding(self):
        det = DriftDetector()
        emb = det._compute_batch_embedding(self._categorical_batch("cat", "b0", "A"))
        assert emb is not None
        assert len(emb) == 256

    def test_categorical_drift_still_detected(self):
        det = DriftDetector()
        det.register_baseline("cat", self._categorical_batch("cat", "base", "A"))
        # Same categorical distribution → no drift
        same = det.detect(self._categorical_batch("cat", "same", "A"))
        assert same.drift_detected is False
        # Dominant category flips A→Z → drift must be caught
        drifted = det.detect(self._categorical_batch("cat", "drift", "Z"))
        assert drifted.drift_detected is True

    def test_mixed_batch_embeds_only_categorical(self):
        det = DriftDetector()
        import random
        rnd = random.Random("mixed")
        rows = [{"value": rnd.gauss(10, 2), "cat": rnd.choice(["A", "B"])}
                for _ in range(200)]
        batch = BatchPayload(source_id="mix", batch_id="b0", rows=rows)
        emb = det._compute_batch_embedding(batch)
        assert emb is not None  # categorical column present
        assert det._categorical_columns(batch) == ["cat"]


class TestVectorizedDistributionIdentity:
    """detect() was vectorized with numpy; distributions and histogram bin
    counts must remain bit-for-bit identical to the pure-Python reference,
    because those counts feed KL divergence and severity classification."""

    _BINS = 50

    @staticmethod
    def _py_reference(values, bins=50):
        """Pure-Python distribution computation (pre-vectorization logic)."""
        mean = sum(values) / len(values)
        std = (sum((x - mean) ** 2 for x in values) / max(len(values), 1)) ** 0.5
        distinct = len(set(values))
        mn, mx = min(values), max(values)
        if mn == mx:
            hist = {"bins": [mn], "counts": [len(values)]}
        else:
            bw = (mx - mn) / bins
            edges = [mn + i * bw for i in range(bins + 1)]
            counts = [0] * bins
            for v in values:
                counts[min(int((v - mn) / bw), bins - 1)] += 1
            hist = {"bins": edges, "counts": counts}
        return mean, std, distinct, hist

    def test_numeric_distribution_matches_reference(self):
        import random
        rnd = random.Random("vec-identity")
        det = DriftDetector()
        for _ in range(5):
            vals = [rnd.gauss(10, 2) for _ in range(5000)]
            rows = [{"value": v} for v in vals]
            batch = BatchPayload(source_id="s", batch_id="b", rows=rows)
            dist = det._compute_distributions(batch)["value"]
            pm, ps, pd, ph = self._py_reference(vals)
            assert abs(dist.mean - pm) < 1e-9
            assert abs(dist.std - ps) < 1e-9
            assert dist.distinct_count == pd
            assert dist.histogram["counts"] == ph["counts"]
            assert sum(dist.histogram["counts"]) == len(vals)

    def test_constant_column_histogram(self):
        det = DriftDetector()
        rows = [{"value": 5.0} for _ in range(100)]
        batch = BatchPayload(source_id="s", batch_id="b", rows=rows)
        dist = det._compute_distributions(batch)["value"]
        assert dist.histogram["counts"] == [100]
        assert dist.std == 0.0

    def test_histogram_accepts_list_and_array(self):
        import numpy as np
        det = DriftDetector()
        vals = [float(x) for x in np.random.default_rng(0).normal(0, 1, 1000)]
        as_list = det._build_numeric_histogram(vals)
        as_array = det._build_numeric_histogram(np.asarray(vals, dtype=float))
        assert as_list["counts"] == as_array["counts"]

    def test_non_numeric_falls_back_to_categorical(self):
        det = DriftDetector()
        rows = [{"value": "A"}, {"value": "B"}, {"value": "A"}]
        batch = BatchPayload(source_id="s", batch_id="b", rows=rows)
        dist = det._compute_distributions(batch)["value"]
        assert "categories" in dist.histogram
        assert dist.histogram["categories"] == {"A": 2, "B": 1}


# ── Cold-start warmup suppression (caveat #1) ─────────────────────

class TestColdStartWarmup:
    """Warmup suppresses the cold-start false-positive transient (Poisson
    bin-noise off an under-sampled 50-bin histogram) WITHOUT hiding genuine
    severe drift, discriminating the two by column location shift.

    Empirically this drops the first-5-batch healthy FPR from ~0.86 to 0.0
    while leaving the steady-state FPR (~0.077) and catastrophic-drift
    detection untouched.
    """

    import random as _random

    def _healthy(self, sid, bid, n=500, seed=0):
        rnd = self._random.Random(f"{sid}:{bid}:{seed}")
        return BatchPayload(
            source_id=sid, batch_id=str(bid),
            rows=[{"amount": rnd.gauss(50.0, 5.0)} for _ in range(n)],
        )

    def _unit_bug(self, sid, bid, factor=1000.0, n=500, seed=0):
        rnd = self._random.Random(f"{sid}:{bid}:{seed}")
        return BatchPayload(
            source_id=sid, batch_id=str(bid),
            rows=[{"amount": rnd.gauss(50.0 * factor, 5.0 * factor)} for _ in range(n)],
        )

    def test_warmup_suppresses_healthy_cold_start(self):
        det = DriftDetector(default_zeta=0.15, warmup_batches=5)
        det.register_baseline("s", self._healthy("s", "base", seed=99))
        # first 5 healthy batches must NOT false-alarm during warmup
        for b in range(1, 6):
            r = det.detect(self._healthy("s", b, seed=b))
            assert r.drift_detected is False, f"cold-start false alarm at batch {b}"

    def test_warmup_does_not_hide_catastrophic_drift(self):
        det = DriftDetector(default_zeta=0.15, warmup_batches=5)
        det.register_baseline("s", self._healthy("s", "base", seed=99))
        # a ×1000 unit bug on batch #1 (deep in warmup) still fires CRITICAL
        r = det.detect(self._unit_bug("s", 1))
        assert r.drift_detected is True
        assert r.drift_type == DriftType.STATISTICAL
        assert r.severity == DriftSeverity.CRITICAL

    def test_warmup_zero_restores_original_behaviour(self):
        det = DriftDetector(default_zeta=0.15, warmup_batches=0)
        det.register_baseline("s", self._healthy("s", "base", seed=99))
        # with warmup off, at least one of the first healthy batches false-alarms
        # (the raw cold-start transient this feature exists to remove)
        fired = any(
            det.detect(self._healthy("s", b, seed=b)).drift_detected
            for b in range(1, 6)
        )
        assert fired is True

    def test_steady_state_drift_fires_after_warmup(self):
        det = DriftDetector(default_zeta=0.15, warmup_batches=3)
        det.register_baseline("s", self._healthy("s", "base", seed=99))
        for b in range(1, 5):  # fill past warmup with healthy data
            det.detect(self._healthy("s", b, seed=b))
        # now a real unit bug post-warmup fires normally
        r = det.detect(self._unit_bug("s", 99))
        assert r.drift_detected is True
        assert r.drift_type == DriftType.STATISTICAL

