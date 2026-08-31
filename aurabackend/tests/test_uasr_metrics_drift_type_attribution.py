"""
Metrics drift-type mis-attribution fix.

Root-caused live (2026-08-31, docs/superpowers/specs/2026-08-31-uasr-live-
validation-and-benchmark.md): a genuine SCHEMA drift event showed up under
the "statistical" bucket in ``GET /uasr/metrics``. Cause:
``record_from_loop_result`` inferred ``drift_type``/``severity`` from
``loop_result.diagnosis`` via ``hasattr(diag, "drift_type")`` --
``DiagnosisResult`` (uasr/models.py) has no ``drift_type`` or ``severity``
field at all, so that check was always False and EVERY event recorded
through this path silently defaulted to STATISTICAL/LOW, for every
recovery, not just failed ones. The fix threads the actual
``DriftDetectionResult`` through explicitly instead of trying to recover it
from an object that structurally never carried it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.metrics import HealingMetricTracker
from uasr.models import (
    DiagnosisResult,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    RecoveryLoopResult,
    RecoveryStatus,
)


def _loop_result(status: RecoveryStatus = RecoveryStatus.FAILED) -> RecoveryLoopResult:
    return RecoveryLoopResult(
        drift_event_id="b1",
        recovery_id="r1",
        status=status,
        diagnosis=DiagnosisResult(
            drift_event_id="b1",
            root_cause="Columns both added and removed — possible rename",
            confidence=0.75,
        ),
        total_latency_seconds=0.05,
    )


def _drift(drift_type: DriftType, severity: DriftSeverity = DriftSeverity.HIGH) -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="s1", batch_id="b1", drift_detected=True,
        drift_type=drift_type, severity=severity,
    )


class TestRecordFromLoopResultDriftTypeAttribution:

    def test_schema_drift_is_recorded_as_schema_not_statistical(self):
        """The exact bug found live: a SCHEMA event must not land in the
        statistical bucket regardless of recovery outcome."""
        tracker = HealingMetricTracker()
        tracker.record_from_loop_result("s1", _loop_result(RecoveryStatus.FAILED), _drift(DriftType.SCHEMA))
        report = tracker.compute()
        sm = report.per_source[0]
        assert "schema" in sm.by_drift_type
        assert "statistical" not in sm.by_drift_type

    def test_severity_is_taken_from_drift_result_not_defaulted(self):
        tracker = HealingMetricTracker()
        tracker.record_from_loop_result("s1", _loop_result(), _drift(DriftType.SCHEMA, DriftSeverity.HIGH))
        assert tracker._events[-1].severity == DriftSeverity.HIGH

    def test_correct_on_a_deployed_recovery_too(self):
        """Not just the failed case -- the old bug affected every call,
        successful or not, since DiagnosisResult never had drift_type."""
        tracker = HealingMetricTracker()
        tracker.record_from_loop_result("s1", _loop_result(RecoveryStatus.DEPLOYED), _drift(DriftType.SEMANTIC))
        assert tracker._events[-1].drift_type == DriftType.SEMANTIC

    def test_falls_back_to_statistical_default_when_no_drift_result_given(self):
        """Backward-compatible fallback for a hypothetical caller with no
        DriftDetectionResult in scope -- every real call site now passes
        one, so this only guards the documented fallback behaviour."""
        tracker = HealingMetricTracker()
        tracker.record_from_loop_result("s1", _loop_result())
        assert tracker._events[-1].drift_type == DriftType.STATISTICAL
