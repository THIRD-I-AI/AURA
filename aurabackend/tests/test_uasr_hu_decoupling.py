"""R3 — Hᵤ metric decoupling regression tests.

Proves the decoupled quality/speed composite fixes three pathologies of the
legacy multiplicative Hᵤ = (Resolved/Total)·log(1 + 1/Latency):

  1. Latency ceiling: legacy score < 0.3 floor above ~2.86s even at 100% heal.
  2. Axis conflation: a fast-mediocre pipeline outranks a slow-perfect one.
  3. No independent alerting: one number can't say *which* axis failed.
"""
import math

from uasr.metrics import HealingMetricTracker, RecoveryEvent
from uasr.models import DriftSeverity, DriftType, RecoveryStatus


def _event(source_id, status, latency):
    return RecoveryEvent(source_id=source_id, drift_type=DriftType.STATISTICAL,
                         severity=DriftSeverity.MEDIUM, status=status,
                         latency_seconds=latency)


def _perfect(tracker, source_id, latency, n=5):
    for _ in range(n):
        tracker.record(_event(source_id, RecoveryStatus.DEPLOYED, latency))


class TestDecoupledCeiling:
    def test_legacy_ceiling_pathology_exists(self):
        """Legacy Hᵤ drops below the 0.3 floor at 3s latency even at 100% heal."""
        t = HealingMetricTracker()
        _perfect(t, "s", latency=3.0)
        r = t.compute()
        assert r.global_resolution_rate == 1.0       # perfect healing
        assert r.hu_score < 0.3                        # yet legacy alerts

    def test_decoupled_clears_floor_within_sla(self):
        """Decoupled composite stays at 1.0 for perfect heal within the SLA."""
        t = HealingMetricTracker(sla_seconds=5.0)
        _perfect(t, "s", latency=3.0)
        r = t.compute()
        assert r.global_quality == 1.0
        assert r.global_speed == 1.0                   # 3s <= 5s SLA
        assert r.hu_composite == 1.0                   # no false alert

    def test_speed_penalizes_only_past_sla(self):
        """Speed saturates at 1 within SLA, degrades linearly past it."""
        t = HealingMetricTracker(sla_seconds=5.0)
        _perfect(t, "s", latency=10.0)                 # 2x SLA
        r = t.compute()
        assert r.global_quality == 1.0                 # quality untouched by latency
        assert abs(r.global_speed - 0.5) < 1e-6        # SLA/latency = 5/10
        assert abs(r.hu_composite - 0.5) < 1e-6


class TestAxisDeconflation:
    def test_slow_perfect_beats_fast_mediocre(self):
        """Decoupled composite ranks a perfect healer above a half-failing one,
        reversing the legacy inversion."""
        # A: perfect heal at 3s.  B: 50% heal at 0.3s.
        ta = HealingMetricTracker(sla_seconds=5.0)
        _perfect(ta, "A", latency=3.0, n=10)
        ra = ta.compute()

        tb = HealingMetricTracker(sla_seconds=5.0)
        for i in range(10):
            status = RecoveryStatus.DEPLOYED if i < 5 else RecoveryStatus.FAILED
            tb.record(_event("B", status, 0.3))
        rb = tb.compute()

        # Legacy inverts: fast-mediocre B scores higher than slow-perfect A.
        assert rb.hu_score > ra.hu_score
        # Decoupled corrects: perfect A scores higher than mediocre B.
        assert ra.hu_composite > rb.hu_composite
        assert ra.hu_composite == 1.0
        assert abs(rb.hu_composite - 0.5) < 1e-6

    def test_quality_and_speed_are_independent(self):
        """Quality reflects heal rate only; speed reflects latency only."""
        t = HealingMetricTracker(sla_seconds=2.0)
        # 80% heal rate, well within SLA
        for i in range(10):
            status = RecoveryStatus.DEPLOYED if i < 8 else RecoveryStatus.FAILED
            t.record(_event("s", status, 0.5))
        r = t.compute()
        assert abs(r.global_quality - 0.8) < 1e-6      # pure heal rate
        assert r.global_speed == 1.0                   # 0.5s well under 2s SLA


class TestDecoupledAlerts:
    def test_quality_breach_alerts_on_quality_axis(self):
        t = HealingMetricTracker(sla_seconds=5.0)
        # Fast but failing: 20% heal at 0.1s
        for i in range(10):
            status = RecoveryStatus.DEPLOYED if i < 2 else RecoveryStatus.FAILED
            t.record(_event("s", status, 0.1))
        alerts = t.check_alerts(quality_floor=0.5, speed_floor=0.5)
        metrics = {a["metric"] for a in alerts}
        assert "global_quality" in metrics             # quality flagged
        assert "global_speed" not in metrics           # speed is fine

    def test_latency_breach_alerts_on_speed_axis(self):
        t = HealingMetricTracker(sla_seconds=1.0)
        _perfect(t, "s", latency=10.0, n=10)           # perfect heal, 10x SLA
        alerts = t.check_alerts(quality_floor=0.5, speed_floor=0.5)
        metrics = {a["metric"] for a in alerts}
        assert "global_speed" in metrics               # speed flagged
        assert "global_quality" not in metrics         # quality is perfect

    def test_healthy_pipeline_no_decoupled_alerts(self):
        t = HealingMetricTracker(sla_seconds=5.0)
        _perfect(t, "s", latency=1.0, n=10)
        alerts = t.check_alerts(quality_floor=0.5, speed_floor=0.5)
        metrics = {a["metric"] for a in alerts}
        assert "global_quality" not in metrics
        assert "global_speed" not in metrics


class TestBackwardCompatibility:
    def test_legacy_hu_score_still_present(self):
        t = HealingMetricTracker()
        _perfect(t, "s", latency=0.5, n=5)
        r = t.compute()
        assert r.hu_score > 0                           # legacy field intact
        assert hasattr(r, "hu_composite")               # new field present
        assert r.trend is not None
        assert r.composite_trend is not None

    def test_per_source_carries_both(self):
        t = HealingMetricTracker(sla_seconds=5.0)
        _perfect(t, "s", latency=1.0, n=5)
        r = t.compute()
        sm = r.per_source[0]
        assert sm.healing_contribution > 0              # legacy per-source
        assert sm.quality_score == 1.0                  # decoupled per-source
        assert sm.speed_score == 1.0
        assert sm.hu_composite == 1.0
