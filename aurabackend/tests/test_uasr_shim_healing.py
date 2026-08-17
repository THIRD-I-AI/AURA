"""
UASR self-healing: two bugs that stopped every recovery from succeeding.

1. The outlier-clip shim (SynthesisActuatorAgent._statistical_shim) hardcoded
   +/-1e9 clip bounds instead of deriving them from the baseline distribution,
   so it never clipped real data and validation always failed with
   "Insufficient improvement: pre_kl=..., post_kl=..., target=<...".

2. RecoveryLoop._validate_shim re-ran drift detection on the SAME live
   DriftDetector the Monitor phase uses. That call appended into
   per-source kl_history and fed the inflated zeta forward, so a failed
   validation corrupted detection for every later event on that source.
   DriftDetector.detect() now takes record_state=False for probe calls.
"""
from __future__ import annotations

import ast
import random

import pytest

from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, DiagnosisResult, DriftType, RecoveryStatus
from uasr.recovery_loop import RecoveryLoop


def _exec_transform(code: str):
    ns: dict = {}
    exec(compile(code, "<shim>", "exec"), ns)  # noqa: S102
    fn = ns.get("transform")
    assert callable(fn)
    return fn


class TestOutlierClipShimHeals:
    """Bug 1: clip bounds must come from the baseline distribution."""

    def _batches(self):
        rnd = random.Random(7)
        baseline_rows = [{"v": rnd.gauss(50.0, 5.0)} for _ in range(300)]
        baseline = BatchPayload(source_id="src", columns=["v"], rows=baseline_rows)

        # Mostly baseline-like values plus a burst of far outliers -- the
        # outliers are exactly what a working clip shim must correct.
        drifted_rows = [{"v": rnd.gauss(50.0, 5.0)} for _ in range(285)]
        drifted_rows += [{"v": rnd.uniform(70_000.0, 90_000.0)} for _ in range(15)]
        drifted = BatchPayload(source_id="src", columns=["v"], rows=drifted_rows)
        return baseline, drifted

    def test_clip_shim_reduces_kl_materially(self):
        det = DriftDetector(default_zeta=0.05)
        baseline, drifted = self._batches()
        det.register_baseline("src", baseline)

        pre_result = det.detect(drifted, record_state=False)
        assert pre_result.drift_detected
        assert pre_result.drift_type == DriftType.STATISTICAL
        pre_kl = pre_result.kl_divergence
        drift_vector = pre_result.drift_vector

        actuator = SynthesisActuatorAgent.__new__(SynthesisActuatorAgent)
        diag = DiagnosisResult(drift_event_id="e1", root_cause="outlier burst")
        code = actuator._statistical_shim(drift_vector, diag)

        assert code is not None
        assert "_CLIP_BOUNDS" in code  # exercising the clip branch, not rescale
        ast.parse(code)
        transform = _exec_transform(code)

        healed_rows = transform([dict(r) for r in drifted.rows])
        healed_batch = BatchPayload(source_id="src", columns=["v"], rows=healed_rows)
        post_result = det.detect(healed_batch, record_state=False)
        post_kl = post_result.kl_divergence if post_result.drift_detected else 0.0

        assert post_kl < pre_kl * 0.5, (
            f"clip shim did not materially reduce KL: pre={pre_kl:.4f} post={post_kl:.4f}"
        )

    def test_clip_bounds_are_per_column_and_degenerate_safe(self):
        """Missing/zero baseline_std must not collapse a column to one point."""
        actuator = SynthesisActuatorAgent.__new__(SynthesisActuatorAgent)
        diag = DiagnosisResult(drift_event_id="e2", root_cause="mixed drift")
        drift_vector = {
            "affected_columns": ["a", "b"],
            "max_kl": 5.0,
            "threshold_zeta": 0.15,
            "col_stats": {
                "a": {"baseline_mean": 50.0, "batch_mean": 51.0, "baseline_std": 5.0},
                "b": {"baseline_mean": 10.0, "batch_mean": 10.0, "baseline_std": 0.0},
            },
        }
        code = actuator._statistical_shim(drift_vector, diag)
        assert code is not None
        transform = _exec_transform(code)

        out = transform([{"a": 1000.0, "b": 1000.0}])
        # column "a" has a real baseline std -> clipped to mean +/- 3*std
        assert out[0]["a"] == pytest.approx(65.0)
        # column "b" has degenerate (zero) std -> falls back to a wide
        # no-op range, NOT collapsed to a single point (e.g. 10.0)
        assert out[0]["b"] == 1000.0


class TestDriftTypeRoutesRepair:
    """Repair must match drift TYPE, not just severity.

    A severe LOCATION shift (the whole batch's centroid moved) must never
    be auto-clipped: clipping to baseline_mean +/- 3*std collapses every
    row onto the clip boundary, making divergence WORSE, not better
    (measured live: N(100,10) -> N(250,10) clipped to a degenerate spike
    at 130, pre_kl=44.6108 -> post_kl=122.6068). It must instead escalate
    to the S41 PENDING_APPROVAL queue -- see
    actuator_agent._location_shift_reason / recovery_loop
    ShimResult.requires_human_review.

    Genuine outlier contamination (a handful of extreme values, but the
    batch mean stays close to baseline) must keep auto-clipping exactly as
    before -- this is the regression guard for that change.
    """

    @staticmethod
    def _baseline_detector(seed: int = 7):
        rnd = random.Random(seed)
        rows = [{"v": rnd.gauss(100.0, 10.0)} for _ in range(300)]
        det = DriftDetector(default_zeta=0.05)
        det.register_baseline("src", BatchPayload(source_id="src", columns=["v"], rows=rows))
        return det, rnd

    @pytest.mark.asyncio
    async def test_location_shift_escalates_to_pending_approval_not_failed(self):
        det, rnd = self._baseline_detector()
        drifted_rows = [{"v": rnd.gauss(250.0, 10.0)} for _ in range(300)]
        drifted = BatchPayload(source_id="src", batch_id="b1", columns=["v"], rows=drifted_rows)

        drift_result = det.detect(drifted, record_state=False)
        assert drift_result.drift_detected
        assert drift_result.drift_type == DriftType.STATISTICAL

        # Default config: risk_tiered=False -- escalation must fire
        # regardless of the S41 risk-tier setting, because auto-transform
        # is objectively wrong here, not a matter of risk tolerance.
        loop = RecoveryLoop(detector=det)
        result = await loop.run(drift_result, drifted)

        # Nothing failed -- a human was asked. Must not be a deployed clip
        # shim, and must not be marked "failed".
        assert result.status == RecoveryStatus.PENDING_APPROVAL
        assert result.status != RecoveryStatus.FAILED
        # Same status value GET /uasr/recovery/pending filters on
        # (service.py pending_approvals: RecoveryRecord.status == this).
        assert result.status.value == "pending_approval"

        assert result.shim is not None
        assert result.shim.deployed is False
        assert result.shim.requires_human_review is True
        assert result.shim.escalation_reason is not None
        assert "location shift" in result.shim.escalation_reason

    @pytest.mark.asyncio
    async def test_outlier_contamination_still_auto_clips_and_reduces_divergence(self):
        det, rnd = self._baseline_detector()
        # 295 baseline-like rows + 5 fixed extreme outliers: individually far
        # from baseline (strong histogram-shape KL), but only ~2.3
        # baseline-sigmas of pull on the batch MEAN -- genuine outlier
        # contamination, not a location shift.
        drifted_rows = [{"v": rnd.gauss(100.0, 10.0)} for _ in range(295)]
        drifted_rows += [{"v": 1500.0} for _ in range(5)]
        drifted = BatchPayload(source_id="src", batch_id="b2", columns=["v"], rows=drifted_rows)

        drift_result = det.detect(drifted, record_state=False)
        assert drift_result.drift_detected
        pre_kl = drift_result.kl_divergence

        loop = RecoveryLoop(detector=det)
        result = await loop.run(drift_result, drifted)

        assert result.status == RecoveryStatus.DEPLOYED
        assert result.shim is not None
        assert result.shim.requires_human_review is False
        assert result.shim.deployed is True
        assert result.shim.post_kl_divergence is not None
        assert result.shim.post_kl_divergence < pre_kl * 0.5, (
            f"clip shim did not materially reduce KL: pre={pre_kl:.4f} "
            f"post={result.shim.post_kl_divergence:.4f}"
        )


class TestDetectRecordStateFalseDoesNotPoisonHistory:
    """Bug 2: validation probes must not mutate kl_history / zeta."""

    def _drifted_batch(self, seed: int):
        rnd = random.Random(seed)
        rows = [{"v": rnd.gauss(500.0, 5.0)} for _ in range(100)]
        return BatchPayload(source_id="src", columns=["v"], rows=rows)

    def test_record_state_false_leaves_history_length_unchanged(self):
        det = DriftDetector(default_zeta=0.05)
        rnd = random.Random(1)
        baseline_rows = [{"v": rnd.gauss(50.0, 5.0)} for _ in range(300)]
        det.register_baseline("src", BatchPayload(source_id="src", columns=["v"], rows=baseline_rows))

        before_len = len(det._kl_history.get("src", []))

        # Probe calls (record_state=False) must not grow kl_history.
        for i in range(3):
            det.detect(self._drifted_batch(seed=100 + i), record_state=False)
        assert len(det._kl_history.get("src", [])) == before_len

        # A real (default) call must still append, proving detect() itself
        # still records state normally.
        det.detect(self._drifted_batch(seed=999))
        assert len(det._kl_history.get("src", [])) == before_len + 1
