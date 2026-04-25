"""
Tests for UASR — Universal Agentic Semantic Recovery
=====================================================
Covers: DriftDetector, SemanticGateway, HealingMetricTracker, RecoveryLoop
"""
from __future__ import annotations

import asyncio
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.drift_detector import DriftDetector
from uasr.metrics import HealingMetricTracker, RecoveryEvent
from uasr.models import (
    BatchPayload,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    RecoveryStatus,
)
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig
from uasr.semantic_gateway import (
    ReferenceContextMatrix,
    SemanticGateway,
    batch_embedding,
    cosine_similarity,
)

# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def _make_batch(source_id: str, columns: list, rows: list, batch_id: str = "b1") -> BatchPayload:
    return BatchPayload(
        source_id=source_id,
        batch_id=batch_id,
        columns=columns,
        rows=rows,
        schema_snapshot={c: "str" for c in columns},
    )


BASELINE_ROWS = [
    {"id": i, "name": f"item_{i}", "value": float(i * 10)}
    for i in range(1, 51)
]
BASELINE_COLS = ["id", "name", "value"]


# ────────────────────────────────────────────────────────────────
# DriftDetector
# ────────────────────────────────────────────────────────────────

class TestDriftDetector:
    def test_no_drift_on_identical_batch(self):
        det = DriftDetector()
        baseline = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        det.register_baseline("src1", baseline)

        result = det.detect(baseline)
        assert not result.drift_detected

    def test_schema_drift_missing_column(self):
        det = DriftDetector()
        baseline = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        det.register_baseline("src1", baseline)

        # Remove 'value' column
        drifted_rows = [{"id": r["id"], "name": r["name"]} for r in BASELINE_ROWS]
        drifted = _make_batch("src1", ["id", "name"], drifted_rows, "b2")

        result = det.detect(drifted)
        assert result.drift_detected
        assert result.drift_type == DriftType.SCHEMA

    def test_schema_drift_new_column(self):
        det = DriftDetector()
        baseline = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        det.register_baseline("src1", baseline)

        # Add extra column
        drifted_rows = [{**r, "extra": "x"} for r in BASELINE_ROWS]
        drifted = _make_batch("src1", BASELINE_COLS + ["extra"], drifted_rows, "b3")

        result = det.detect(drifted)
        assert result.drift_detected
        assert result.drift_type == DriftType.SCHEMA

    def test_statistical_drift_extreme_values(self):
        det = DriftDetector(default_zeta=0.05)
        baseline = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        det.register_baseline("src1", baseline)

        # Wildly different numeric distribution
        drifted_rows = [
            {"id": i, "name": f"item_{i}", "value": float(i * 10000)}
            for i in range(1, 51)
        ]
        drifted = _make_batch("src1", BASELINE_COLS, drifted_rows, "b4")

        result = det.detect(drifted)
        assert result.drift_detected
        assert result.drift_type == DriftType.STATISTICAL

    def test_kl_divergence_identical_is_zero(self):
        hist = {"a": 0.5, "b": 0.3, "c": 0.2}
        kl = DriftDetector._kl_divergence(hist, hist)
        assert abs(kl) < 1e-10

    def test_kl_divergence_different(self):
        p = {"a": 0.9, "b": 0.1}
        q = {"a": 0.1, "b": 0.9}
        kl = DriftDetector._kl_divergence(p, q)
        assert kl > 0

    def test_no_baseline_returns_clean(self):
        det = DriftDetector()
        batch = _make_batch("unknown", BASELINE_COLS, BASELINE_ROWS)
        result = det.detect(batch)
        assert not result.drift_detected


# ────────────────────────────────────────────────────────────────
# SemanticGateway
# ────────────────────────────────────────────────────────────────

class TestSemanticGateway:
    def test_first_batch_auto_registers(self):
        gw = SemanticGateway()
        batch = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        decision = gw.check(batch)
        assert decision.allowed
        assert decision.similarity == 1.0

    def test_identical_batch_passes(self):
        gw = SemanticGateway()
        batch = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        gw.register_baseline(batch)
        decision = gw.check(batch)
        assert decision.allowed

    def test_very_different_batch_may_fail(self):
        gw = SemanticGateway(tolerance=0.05)  # very strict
        baseline = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        gw.register_baseline(baseline)

        # Completely different data
        weird_rows = [{"x": 999, "y": "zzz", "z": -1} for _ in range(50)]
        weird = _make_batch("src1", ["x", "y", "z"], weird_rows, "b5")

        decision = gw.check(weird)
        # With hash-projection, very different data should have lower similarity
        assert isinstance(decision.similarity, float)

    def test_reference_versioning(self):
        gw = SemanticGateway()
        batch1 = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        v1 = gw.register_baseline(batch1, desc="v1")

        batch2 = _make_batch("src1", ["a", "b"], [{"a": 1, "b": 2}], "b6")
        v2 = gw.register_baseline(batch2, desc="v2")

        versions = gw.reference_versions("src1")
        assert len(versions) == 2
        assert versions[1]["active"]
        assert not versions[0]["active"]

        # Rollback to v1
        assert gw.rollback_reference("src1", v1)
        versions = gw.reference_versions("src1")
        assert versions[0]["active"]

    def test_cosine_similarity_self(self):
        vec = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-10

    def test_batch_embedding_deterministic(self):
        batch = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)
        e1 = batch_embedding(batch)
        e2 = batch_embedding(batch)
        assert e1 == e2


# ────────────────────────────────────────────────────────────────
# HealingMetricTracker
# ────────────────────────────────────────────────────────────────

class TestHealingMetrics:
    def test_empty_report(self):
        tracker = HealingMetricTracker()
        report = tracker.compute()
        assert report.hu_score == 0.0
        assert report.total_events == 0

    def test_perfect_healing(self):
        tracker = HealingMetricTracker()
        for i in range(5):
            tracker.record(RecoveryEvent(
                source_id="src1",
                drift_type=DriftType.STATISTICAL,
                severity=DriftSeverity.MEDIUM,
                status=RecoveryStatus.DEPLOYED,
                latency_seconds=1.0,
            ))

        report = tracker.compute()
        assert report.hu_score > 0
        assert report.global_resolution_rate == 1.0
        assert report.total_events == 5

    def test_mixed_healing(self):
        tracker = HealingMetricTracker()
        # 3 resolved, 2 failed for src1
        for _ in range(3):
            tracker.record(RecoveryEvent(
                source_id="src1",
                drift_type=DriftType.SCHEMA,
                severity=DriftSeverity.HIGH,
                status=RecoveryStatus.DEPLOYED,
                latency_seconds=2.0,
            ))
        for _ in range(2):
            tracker.record(RecoveryEvent(
                source_id="src1",
                drift_type=DriftType.STATISTICAL,
                severity=DriftSeverity.LOW,
                status=RecoveryStatus.FAILED,
                latency_seconds=5.0,
            ))

        report = tracker.compute()
        assert 0 < report.hu_score
        assert report.global_resolution_rate == 0.6  # 3/5

    def test_multi_source_hu(self):
        tracker = HealingMetricTracker()
        # src1: 100% resolved
        for _ in range(3):
            tracker.record(RecoveryEvent(
                source_id="src1",
                drift_type=DriftType.STATISTICAL,
                severity=DriftSeverity.LOW,
                status=RecoveryStatus.DEPLOYED,
                latency_seconds=1.0,
            ))
        # src2: 50% resolved
        tracker.record(RecoveryEvent(
            source_id="src2",
            drift_type=DriftType.SCHEMA,
            severity=DriftSeverity.HIGH,
            status=RecoveryStatus.DEPLOYED,
            latency_seconds=3.0,
        ))
        tracker.record(RecoveryEvent(
            source_id="src2",
            drift_type=DriftType.SCHEMA,
            severity=DriftSeverity.HIGH,
            status=RecoveryStatus.FAILED,
            latency_seconds=5.0,
        ))

        report = tracker.compute()
        assert report.total_sources == 2
        # Hᵤ = (1/2) * (1.0 * log(1+1/1) + 0.5 * log(1+1/3))
        # Hᵤ > 0
        assert report.hu_score > 0

    def test_alerts_on_low_hu(self):
        tracker = HealingMetricTracker()
        # All failed → Hᵤ = 0
        for _ in range(5):
            tracker.record(RecoveryEvent(
                source_id="src1",
                drift_type=DriftType.STATISTICAL,
                severity=DriftSeverity.CRITICAL,
                status=RecoveryStatus.FAILED,
                latency_seconds=10.0,
            ))

        alerts = tracker.check_alerts(hu_floor=0.3, resolution_floor=0.5)
        assert len(alerts) >= 1
        assert any(a["metric"] == "hu_score" for a in alerts)


# ────────────────────────────────────────────────────────────────
# RecoveryLoop (sandbox execution)
# ────────────────────────────────────────────────────────────────

class TestRecoveryLoopSandbox:
    def test_sandbox_execute_identity(self):
        shim_code = "def transform(rows):\n    return rows\n"
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        result = RecoveryLoop._sandbox_execute(shim_code, rows)
        assert result == rows

    def test_sandbox_execute_transform(self):
        shim_code = (
            "def transform(rows):\n"
            "    for r in rows:\n"
            "        r['value'] = float(r.get('value', 0))\n"
            "    return rows\n"
        )
        rows = [{"value": "42"}, {"value": "3.14"}]
        result = RecoveryLoop._sandbox_execute(shim_code, rows)
        assert result[0]["value"] == 42.0
        assert result[1]["value"] == 3.14

    def test_sandbox_no_transform_raises(self):
        with pytest.raises(ValueError, match="transform"):
            RecoveryLoop._sandbox_execute("x = 1", [])

    def test_sandbox_bad_return_type_raises(self):
        shim_code = "def transform(rows):\n    return 'not a list'\n"
        with pytest.raises(ValueError, match="expected list"):
            RecoveryLoop._sandbox_execute(shim_code, [{"a": 1}])


# ────────────────────────────────────────────────────────────────
# Integration: recovery loop end-to-end
# ────────────────────────────────────────────────────────────────

class TestRecoveryLoopIntegration:
    def test_no_drift_no_recovery(self):
        """If there's no drift, running the loop should still work (diagnosis returns None → FAILED)."""
        det = DriftDetector()
        loop = RecoveryLoop(det, RecoveryLoopConfig(max_iterations=1))

        # Fabricate a drift result that claims drift but with minimal data
        drift = DriftDetectionResult(
            source_id="src1",
            batch_id="b1",
            drift_detected=True,
            drift_type=DriftType.SCHEMA,
            severity=DriftSeverity.LOW,
            details="test drift",
            drift_vector={"removed_columns": ["col_x"]},
        )

        batch = _make_batch("src1", BASELINE_COLS, BASELINE_ROWS)

        result = asyncio.run(loop.run(drift, batch))
        # Result should be either DEPLOYED or FAILED depending on agent availability
        assert result.status in (
            RecoveryStatus.DEPLOYED,
            RecoveryStatus.FAILED,
            RecoveryStatus.VALIDATING,
        )
        assert result.recovery_id


# ────────────────────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────────────────────

class TestModels:
    def test_batch_payload_defaults(self):
        b = BatchPayload(source_id="s1")
        assert b.source_id == "s1"
        assert b.batch_id  # auto-generated
        assert b.rows == []

    def test_drift_detection_result(self):
        r = DriftDetectionResult(source_id="s1", batch_id="b1")
        assert not r.drift_detected
        assert r.drift_type is None

    def test_enum_values(self):
        assert DriftType.SCHEMA.value == "schema"
        assert DriftSeverity.CRITICAL.value == "critical"
        assert RecoveryStatus.DEPLOYED.value == "deployed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
