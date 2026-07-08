"""
UASR Metrics — Universal Healing Coefficient (Hᵤ) & Observability
===================================================================
Implements the healing quality metric from UASR §5:

    Hᵤ (legacy)    = (1 / D) · Σᵢ (Resolvedᵢ / Totalᵢ) · log(1 + 1/Latencyᵢ)
    Hᵤ (decoupled) = (1 / D) · Σᵢ Qᵢ · Sᵢ

Where:
  - D = number of data sources being monitored
  - Resolved / Total = resolution rate per source
  - Latency = average recovery latency in seconds
  - Q (quality) = Resolved / Total                    -- unitless heal rate [0, 1]
  - S (speed)   = min(1, SLA / max(Latency, ε))        -- SLA attainment [0, 1]

The legacy Hᵤ multiplies resolution by log(1 + 1/Latency), which (a) conflates
healing *quality* with *speed* on one axis and (b) has a hard latency ceiling:
above ~2.86 s average latency the legacy score sits below its own 0.3 alert
floor even at 100% resolution, so any pipeline with legitimate multi-second
recovery (LLM shim synthesis + canary) alerts as unhealthy regardless of how
perfectly it heals. The decoupled form reports Q and S separately, normalizes
latency to an explicit SLA budget (latency only penalizes once the SLA is
*missed*), and alerts on each axis independently. `hu_score` is retained for
backward compatibility; `hu_composite`, `global_quality`, `global_speed` are
the decoupled successors.

Plus:
  - Per-source and per-drift-type breakdown
  - Trend tracking (rolling windows)
  - Alert thresholds
"""
from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .models import DriftSeverity, DriftType, RecoveryStatus

logger = logging.getLogger("uasr.metrics")


# ────────────────────────────────────────────────────────────────────
# Data structures
# ────────────────────────────────────────────────────────────────────

@dataclass
class RecoveryEvent:
    """A single recovery event recorded for metrics."""
    source_id: str
    drift_type: DriftType
    severity: DriftSeverity
    status: RecoveryStatus
    latency_seconds: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    recovery_id: str = ""
    post_kl: float = 0.0


@dataclass
class SourceMetrics:
    """Aggregated metrics for a single data source."""
    source_id: str
    total_events: int = 0
    resolved_events: int = 0
    failed_events: int = 0
    avg_latency: float = 0.0
    resolution_rate: float = 0.0
    healing_contribution: float = 0.0   # This source's legacy Hᵤ contribution
    quality_score: float = 0.0          # Q = resolution rate (decoupled)
    speed_score: float = 0.0            # S = SLA attainment min(1, SLA/latency)
    hu_composite: float = 0.0           # Q · S (decoupled per-source score)
    by_drift_type: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class HealingReport:
    """Full Hᵤ report across all sources."""
    hu_score: float = 0.0
    total_sources: int = 0
    total_events: int = 0
    resolved_events: int = 0
    global_resolution_rate: float = 0.0
    global_avg_latency: float = 0.0
    hu_composite: float = 0.0               # decoupled (1/D)·Σ Qᵢ·Sᵢ
    global_quality: float = 0.0             # mean per-source quality Q
    global_speed: float = 0.0               # mean per-source speed S
    sla_seconds: float = 0.0                # SLA budget used for S
    per_source: List[SourceMetrics] = field(default_factory=list)
    computed_at: str = ""
    trend: Optional[List[float]] = None     # rolling Hᵤ values
    composite_trend: Optional[List[float]] = None  # rolling hu_composite values


# ────────────────────────────────────────────────────────────────────
# Hᵤ Tracker
# ────────────────────────────────────────────────────────────────────

class HealingMetricTracker:
    """
    Collects recovery events and computes the Universal Healing Coefficient.

    Usage:
        tracker = HealingMetricTracker()
        tracker.record(event)
        report = tracker.compute()
        print(report.hu_score)
    """

    def __init__(self, trend_window: int = 50, sla_seconds: float = 5.0) -> None:
        self._events: List[RecoveryEvent] = []
        self._trend_window = trend_window
        self._sla_seconds = sla_seconds
        self._hu_history: List[float] = []
        self._composite_history: List[float] = []

    # ── Recording ───────────────────────────────────────────────────

    def record(self, event: RecoveryEvent) -> None:
        """Record a recovery event."""
        self._events.append(event)
        logger.debug(
            "Recorded recovery event: source=%s, type=%s, status=%s, latency=%.2fs",
            event.source_id,
            event.drift_type.value,
            event.status.value,
            event.latency_seconds,
        )

    def record_from_loop_result(self, source_id: str, loop_result) -> None:
        """Convenience: record directly from a RecoveryLoopResult."""
        from .models import RecoveryLoopResult

        drift_type = DriftType.STATISTICAL   # default
        severity = DriftSeverity.LOW
        if loop_result.diagnosis:
            diag = loop_result.diagnosis
            if hasattr(diag, "drift_type"):
                drift_type = diag.drift_type if isinstance(diag.drift_type, DriftType) else DriftType.STATISTICAL
            if hasattr(diag, "severity"):
                severity = diag.severity if isinstance(diag.severity, DriftSeverity) else DriftSeverity.LOW

        event = RecoveryEvent(
            source_id=source_id,
            drift_type=drift_type,
            severity=severity,
            status=loop_result.status,
            latency_seconds=loop_result.total_latency_seconds or 0.0,
            recovery_id=loop_result.recovery_id or "",
            post_kl=loop_result.shim.post_kl_divergence if loop_result.shim else 0.0,
        )
        self.record(event)

    # ── Computation ─────────────────────────────────────────────────

    def compute(self, window_seconds: Optional[float] = None) -> HealingReport:
        """
        Compute the full Hᵤ report.

        Args:
            window_seconds: If set, only consider events within this window.
        """
        events = self._events
        if window_seconds is not None:
            cutoff = time.time() - window_seconds
            events = [
                e for e in events
                if e.timestamp.timestamp() >= cutoff
            ]

        if not events:
            return HealingReport(computed_at=datetime.now(timezone.utc).isoformat())

        # Group by source
        by_source: Dict[str, List[RecoveryEvent]] = defaultdict(list)
        for e in events:
            by_source[e.source_id].append(e)

        D = len(by_source)
        hu_sum = 0.0
        composite_sum = 0.0
        quality_sum = 0.0
        speed_sum = 0.0
        per_source: List[SourceMetrics] = []
        total_resolved = 0
        total_all = 0
        all_latencies: List[float] = []

        for source_id, source_events in by_source.items():
            total = len(source_events)
            resolved = sum(
                1 for e in source_events
                if e.status == RecoveryStatus.DEPLOYED
            )
            failed = sum(
                1 for e in source_events
                if e.status == RecoveryStatus.FAILED
            )

            latencies = [e.latency_seconds for e in source_events if e.latency_seconds > 0]
            avg_lat = statistics.mean(latencies) if latencies else 1.0

            resolution_rate = resolved / total if total > 0 else 0.0

            # Legacy Hᵤ contribution: Hᵤᵢ = (Resolved/Total) · log(1 + 1/Latency)
            contribution = resolution_rate * math.log(1 + 1 / max(avg_lat, 0.01))

            # Decoupled scores: quality and speed on separate, unitless [0,1] axes.
            # Speed normalizes latency to the SLA budget and saturates at 1, so
            # latency only penalizes the score once the SLA is actually missed.
            q_score = resolution_rate
            s_score = min(1.0, self._sla_seconds / max(avg_lat, 1e-3))
            composite = q_score * s_score

            # Per drift-type breakdown
            type_breakdown: Dict[str, Dict[str, Any]] = {}
            type_groups: Dict[str, List[RecoveryEvent]] = defaultdict(list)
            for e in source_events:
                type_groups[e.drift_type.value].append(e)

            for dtype, devents in type_groups.items():
                dt_total = len(devents)
                dt_resolved = sum(1 for e in devents if e.status == RecoveryStatus.DEPLOYED)
                dt_latencies = [e.latency_seconds for e in devents if e.latency_seconds > 0]
                type_breakdown[dtype] = {
                    "total": dt_total,
                    "resolved": dt_resolved,
                    "resolution_rate": round(dt_resolved / dt_total, 4) if dt_total else 0,
                    "avg_latency": round(statistics.mean(dt_latencies), 3) if dt_latencies else 0,
                }

            sm = SourceMetrics(
                source_id=source_id,
                total_events=total,
                resolved_events=resolved,
                failed_events=failed,
                avg_latency=round(avg_lat, 3),
                resolution_rate=round(resolution_rate, 4),
                healing_contribution=round(contribution, 6),
                quality_score=round(q_score, 4),
                speed_score=round(s_score, 4),
                hu_composite=round(composite, 6),
                by_drift_type=type_breakdown,
            )
            per_source.append(sm)

            hu_sum += contribution
            composite_sum += composite
            quality_sum += q_score
            speed_sum += s_score
            total_resolved += resolved
            total_all += total
            all_latencies.extend(latencies)

        hu_score = hu_sum / D if D > 0 else 0.0
        hu_composite = composite_sum / D if D > 0 else 0.0
        global_quality = quality_sum / D if D > 0 else 0.0
        global_speed = speed_sum / D if D > 0 else 0.0

        # Record trends
        self._hu_history.append(hu_score)
        self._composite_history.append(hu_composite)

        report = HealingReport(
            hu_score=round(hu_score, 6),
            total_sources=D,
            total_events=total_all,
            resolved_events=total_resolved,
            global_resolution_rate=round(total_resolved / total_all, 4) if total_all else 0,
            global_avg_latency=round(statistics.mean(all_latencies), 3) if all_latencies else 0,
            hu_composite=round(hu_composite, 6),
            global_quality=round(global_quality, 4),
            global_speed=round(global_speed, 4),
            sla_seconds=self._sla_seconds,
            per_source=per_source,
            computed_at=datetime.now(timezone.utc).isoformat(),
            trend=list(self._hu_history[-self._trend_window:]),
            composite_trend=list(self._composite_history[-self._trend_window:]),
        )

        logger.info("Hᵤ computed: %.6f (D=%d, events=%d)", hu_score, D, total_all)
        return report

    # ── Alerts ──────────────────────────────────────────────────────

    def check_alerts(
        self,
        hu_floor: float = 0.3,
        resolution_floor: float = 0.5,
        quality_floor: float = 0.5,
        speed_floor: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Check if Hᵤ or resolution rate has dropped below alert thresholds.
        Returns a list of alert dicts.
        """
        report = self.compute()
        alerts: List[Dict[str, Any]] = []

        if report.hu_score < hu_floor and report.total_events > 0:
            alerts.append({
                "level": "critical",
                "metric": "hu_score",
                "value": report.hu_score,
                "threshold": hu_floor,
                "message": f"Hᵤ ({report.hu_score:.4f}) is below floor ({hu_floor})",
            })

        # Decoupled alerts: quality and speed breach independently, so an operator
        # sees *which* axis failed rather than a single conflated number.
        if report.total_events > 0 and report.global_quality < quality_floor:
            alerts.append({
                "level": "critical",
                "metric": "global_quality",
                "value": report.global_quality,
                "threshold": quality_floor,
                "message": (
                    f"Healing quality ({report.global_quality:.2%}) is below floor "
                    f"({quality_floor:.2%}) — heals are failing, independent of speed"
                ),
            })

        if report.total_events > 0 and report.global_speed < speed_floor:
            alerts.append({
                "level": "warning",
                "metric": "global_speed",
                "value": report.global_speed,
                "threshold": speed_floor,
                "message": (
                    f"Healing speed ({report.global_speed:.2%} of SLA {report.sla_seconds:.1f}s) "
                    f"is below floor ({speed_floor:.2%}) — heals succeed but miss the SLA"
                ),
            })

        if report.global_resolution_rate < resolution_floor and report.total_events > 0:
            alerts.append({
                "level": "warning",
                "metric": "global_resolution_rate",
                "value": report.global_resolution_rate,
                "threshold": resolution_floor,
                "message": (
                    f"Global resolution rate ({report.global_resolution_rate:.2%}) "
                    f"is below floor ({resolution_floor:.2%})"
                ),
            })

        # Per-source degradation
        for sm in report.per_source:
            if sm.total_events >= 3 and sm.resolution_rate < resolution_floor:
                alerts.append({
                    "level": "warning",
                    "metric": "source_resolution_rate",
                    "source_id": sm.source_id,
                    "value": sm.resolution_rate,
                    "threshold": resolution_floor,
                    "message": (
                        f"Source '{sm.source_id}' resolution rate "
                        f"({sm.resolution_rate:.2%}) is below floor"
                    ),
                })

        # Trend degradation (Hᵤ declining over last 5 readings)
        if len(self._hu_history) >= 5:
            recent = self._hu_history[-5:]
            if all(recent[i] > recent[i + 1] for i in range(len(recent) - 1)):
                alerts.append({
                    "level": "warning",
                    "metric": "hu_trend",
                    "value": recent,
                    "message": "Hᵤ has been declining for the last 5 computations",
                })

        return alerts

    # ── Utilities ───────────────────────────────────────────────────

    @property
    def event_count(self) -> int:
        return len(self._events)

    def reset(self) -> None:
        """Clear all recorded events and history."""
        self._events.clear()
        self._hu_history.clear()
        self._composite_history.clear()
