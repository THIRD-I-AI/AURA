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
