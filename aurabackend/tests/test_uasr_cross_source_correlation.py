"""
Cross-source drift correlation (candidate #5 of the UASR self-healing gap
analysis, docs/superpowers/specs/2026-08-30-uasr-effective-self-healing-
gap-analysis.md).

Before this: each source's drift was analyzed in isolation -- no signal for
"these N sources drifted together, this looks like one upstream incident,"
the pattern a human data engineer would notice first by eyeballing
timestamps.

Report-only base (HealingMetricTracker.detect_correlation):
* Off by default (correlation_window_seconds=0) -- always returns None.
* N+ distinct sources within the window -- an incident is returned.
* Fewer than min_sources distinct sources -- no incident, even with many
  events (repeats from one source don't count as multiple sources).
* Events outside the window are excluded from the count.
* The passive hook (record()) logs exactly once per distinct incident, not
  once per event, and re-arms if the source-set changes.

Cross-source auto-heal (find_recent_deployed_shim +
RecoveryLoop.run_with_candidate_shim):
* find_recent_deployed_shim only returns a DEPLOYED sibling with matching
  drift_type, excluding the requesting source, within the window.
* run_with_candidate_shim: validation failure never deploys; validation
  success deploys under risk_tiered=False; validation success HOLDS under
  risk_tiered=True (the existing S41 gate does the safety work for a
  "cross_source_borrowed" shim with zero new logic, proven here).
"""
from __future__ import annotations

import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.metrics import HealingMetricTracker, RecoveryEvent
from uasr.models import (
    BatchPayload,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    RecoveryStatus,
)
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig


def _event(
    source_id: str,
    drift_type: DriftType = DriftType.SCHEMA,
    status: RecoveryStatus = RecoveryStatus.DEPLOYED,
    shim_code: str | None = "return rows",
) -> RecoveryEvent:
    return RecoveryEvent(
        source_id=source_id,
        drift_type=drift_type,
        severity=DriftSeverity.MEDIUM,
        status=status,
        latency_seconds=0.1,
        shim_code=shim_code if status == RecoveryStatus.DEPLOYED else None,
    )


class TestDetectCorrelation:

    def test_off_by_default(self):
        tracker = HealingMetricTracker()  # correlation_window_seconds=0.0
        for i in range(5):
            tracker.record(_event(f"s{i}"))
        assert tracker.detect_correlation() is None
        # Explicit override still works even when the tracker default is off.
        assert tracker.detect_correlation(window_seconds=60, min_sources=3) is not None

    def test_enough_distinct_sources_returns_an_incident(self):
        tracker = HealingMetricTracker(correlation_window_seconds=60, correlation_min_sources=3)
        for i in range(3):
            tracker.record(_event(f"s{i}", drift_type=DriftType.SCHEMA))
        incident = tracker.detect_correlation()
        assert incident is not None
        assert incident.source_ids == ["s0", "s1", "s2"]
        assert incident.drift_types == ["schema"]

    def test_repeats_from_one_source_do_not_count_as_multiple_sources(self):
        tracker = HealingMetricTracker(correlation_window_seconds=60, correlation_min_sources=3)
        for _ in range(10):
            tracker.record(_event("noisy_source"))
        assert tracker.detect_correlation() is None

    def test_events_outside_the_window_are_excluded(self):
        tracker = HealingMetricTracker(correlation_window_seconds=0.05, correlation_min_sources=3)
        for i in range(3):
            tracker.record(_event(f"s{i}"))
        import time
        time.sleep(0.1)
        assert tracker.detect_correlation() is None

    def test_below_min_sources_is_not_an_incident(self):
        tracker = HealingMetricTracker(correlation_window_seconds=60, correlation_min_sources=3)
        tracker.record(_event("s0"))
        tracker.record(_event("s1"))
        assert tracker.detect_correlation() is None


class TestPassiveHookDedup:

    def test_logs_once_per_distinct_incident(self, caplog):
        tracker = HealingMetricTracker(correlation_window_seconds=60, correlation_min_sources=3)
        with caplog.at_level(logging.WARNING, logger="uasr.metrics"):
            tracker.record(_event("s0"))
            tracker.record(_event("s1"))
            tracker.record(_event("s2"))  # incident now exists: {s0,s1,s2}
            tracker.record(_event("s0"))  # same set, must not re-log
        upstream_warnings = [r for r in caplog.records if "upstream incident" in r.message]
        assert len(upstream_warnings) == 1

    def test_relogs_when_the_source_set_changes(self, caplog):
        tracker = HealingMetricTracker(correlation_window_seconds=60, correlation_min_sources=3)
        with caplog.at_level(logging.WARNING, logger="uasr.metrics"):
            tracker.record(_event("s0"))
            tracker.record(_event("s1"))
            tracker.record(_event("s2"))  # {s0,s1,s2}
            tracker.record(_event("s3"))  # {s0,s1,s2,s3} -- a NEW set
        upstream_warnings = [r for r in caplog.records if "upstream incident" in r.message]
        assert len(upstream_warnings) == 2


class TestFindRecentDeployedShim:

    def test_returns_none_with_no_events(self):
        tracker = HealingMetricTracker()
        assert tracker.find_recent_deployed_shim(DriftType.SCHEMA, "s_failing", 60) is None

    def test_finds_a_matching_sibling(self):
        tracker = HealingMetricTracker()
        tracker.record(_event("s_sibling", drift_type=DriftType.SCHEMA, shim_code="fix_v1"))
        result = tracker.find_recent_deployed_shim(DriftType.SCHEMA, "s_failing", 60)
        assert result == ("s_sibling", "fix_v1")

    def test_excludes_the_requesting_source(self):
        tracker = HealingMetricTracker()
        tracker.record(_event("s_failing", drift_type=DriftType.SCHEMA, shim_code="own_fix"))
        assert tracker.find_recent_deployed_shim(DriftType.SCHEMA, "s_failing", 60) is None

    def test_ignores_non_deployed_events(self):
        tracker = HealingMetricTracker()
        tracker.record(_event("s_sibling", drift_type=DriftType.SCHEMA, status=RecoveryStatus.FAILED))
        assert tracker.find_recent_deployed_shim(DriftType.SCHEMA, "s_failing", 60) is None

    def test_ignores_mismatched_drift_type(self):
        tracker = HealingMetricTracker()
        tracker.record(_event("s_sibling", drift_type=DriftType.STATISTICAL, shim_code="fix_v1"))
        assert tracker.find_recent_deployed_shim(DriftType.SCHEMA, "s_failing", 60) is None

    def test_respects_the_window(self):
        tracker = HealingMetricTracker()
        tracker.record(_event("s_sibling", drift_type=DriftType.SCHEMA, shim_code="fix_v1"))
        import time
        time.sleep(0.1)
        assert tracker.find_recent_deployed_shim(DriftType.SCHEMA, "s_failing", 0.05) is None


def _drift(source_id: str = "s_failing") -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id=source_id,
        batch_id="batch_001",
        drift_detected=True,
        drift_type=DriftType.SCHEMA,
        severity="high",
    )


def _batch(source_id: str = "s_failing") -> BatchPayload:
    return BatchPayload(source_id=source_id, batch_id="batch_001", rows=[{"a": 1}])


class TestRunWithCandidateShim:

    @pytest.mark.asyncio
    async def test_validation_failure_never_deploys(self):
        loop = RecoveryLoop(detector=MagicMock(), config=RecoveryLoopConfig())
        with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {"passed": False, "reason": "doesn't fit"}
            result = await loop.run_with_candidate_shim(
                _drift(), _batch(), "return rows", "s_sibling",
            )
        assert result.status == RecoveryStatus.FAILED
        assert result.shim.generation_method == "cross_source_borrowed"

    @pytest.mark.asyncio
    async def test_validation_success_deploys_when_risk_tiered_off(self):
        loop = RecoveryLoop(
            detector=MagicMock(),
            config=RecoveryLoopConfig(auto_deploy=True, risk_tiered=False),
        )
        with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {"passed": True, "post_kl": 0.01}
            result = await loop.run_with_candidate_shim(
                _drift(), _batch(), "return rows", "s_sibling",
            )
        assert result.status == RecoveryStatus.DEPLOYED
        assert "return rows" in loop.get_deployed_shims("s_failing")

    @pytest.mark.asyncio
    async def test_validation_success_holds_for_approval_when_risk_tiered(self):
        """Proves the existing S41 gate does the safety work with zero new
        logic: a non-'template' generation_method is rejected by
        _should_deploy under risk_tiered=True regardless of severity."""
        from uasr.models import RecoveryMode

        loop = RecoveryLoop(
            detector=MagicMock(),
            config=RecoveryLoopConfig(
                auto_deploy=True, risk_tiered=True, mode=RecoveryMode.AUTO,
            ),
        )
        with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {"passed": True, "post_kl": 0.01}
            result = await loop.run_with_candidate_shim(
                _drift(), _batch(), "return rows", "s_sibling",
            )
        assert result.status == RecoveryStatus.PENDING_APPROVAL
        assert loop.get_deployed_shims("s_failing") == []
