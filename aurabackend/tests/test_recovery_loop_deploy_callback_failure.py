"""
BUG regression -- UASR detects drift but does not heal because
``_deploy_shim`` (and ``deploy_approved_shim``) swallowed any exception
raised by the ``on_shim_deployed`` callback with a bare ``logger.warning``,
then still reported ``RecoveryStatus.DEPLOYED``. The callback is what
persists the recovery record / registers the route with ShimRouter, so a
failure there meant the loop lied: it claimed a heal happened while the
downstream effect (recovery record, ShimRouter route) silently never did.
That's exactly the "drift detected, no shim deployed, no recovery record
created" symptom -- drift keeps re-firing on the very next batch because
nothing ever actually got wired up.

Drives a REAL drift -> diagnose -> generate -> validate -> deploy loop end
to end (only ``on_shim_deployed`` itself is faked, to inject the downstream
failure deterministically), matching the direct-RecoveryLoop pattern in
test_recovery_loop_causal_rl.py.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.models import BatchPayload, DriftDetectionResult, DriftType, RecoveryStatus, ShimResult
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig


def _drift_result() -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="test_src",
        batch_id="batch_001",
        drift_detected=True,
        drift_type=DriftType.SCHEMA,
        severity="high",
        details="column renamed",
        drift_vector={"col": "old_name"},
    )


def _batch() -> BatchPayload:
    return BatchPayload(
        source_id="test_src",
        batch_id="batch_001",
        rows=[{"a": 1}, {"a": 2}, {"a": 3}],
    )


class _FakeDetector:
    def detect(self, batch):
        return DriftDetectionResult(
            source_id=batch.source_id,
            batch_id=batch.batch_id,
            drift_detected=False,
            drift_type=DriftType.DISTRIBUTION,
            severity="low",
            details="",
            drift_vector={},
            kl_divergence=0.01,
        )


def _wire_real_loop(on_shim_deployed) -> RecoveryLoop:
    """A RecoveryLoop whose reflector/actuator are mocked (no LLM needed)
    but which otherwise runs the real diagnose -> generate -> validate ->
    deploy control flow in ``run()``."""
    cfg = RecoveryLoopConfig(max_iterations=3, use_causal_rl_evaluator=False)
    loop = RecoveryLoop(detector=_FakeDetector(), config=cfg, on_shim_deployed=on_shim_deployed)
    loop._reflector = MagicMock()
    loop._reflector.execute = AsyncMock(return_value=MagicMock(
        succeeded=True, artifacts={"diagnosis": MagicMock(model_dump=lambda: {})},
    ))
    loop._actuator = MagicMock()
    loop._actuator.execute = AsyncMock(return_value=MagicMock(
        succeeded=True,
        artifacts={"shim": ShimResult(recovery_id="test_recovery_001", shim_code="return rows")},
    ))
    return loop


class TestDeployCallbackFailureFailsClosed:

    @pytest.mark.asyncio
    async def test_real_drift_to_real_heal_reports_failed_when_downstream_effect_fails(self) -> None:
        """End-to-end: a real drift event drives the loop through
        diagnose/generate/validate/deploy. The ``on_shim_deployed`` callback
        (standing in for persisting the recovery record + registering the
        ShimRouter route) raises. The loop must NOT report DEPLOYED for a
        heal whose downstream effect never happened."""
        def _failing_callback(source_id, shim_code, recovery_id):
            raise RuntimeError("recovery-record persistence failed")

        loop = _wire_real_loop(_failing_callback)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                loop, "_validate_shim",
                AsyncMock(return_value={"passed": True, "post_kl": 0.01}),
            )
            result = await loop.run(_drift_result(), _batch())

        assert result.status == RecoveryStatus.FAILED, (
            f"expected FAILED when the deployment callback raised, got {result.status}"
        )
        # The shim must not be left marked as live, and the registry entry
        # added just before the callback ran must be rolled back -- otherwise
        # get_deployed_shims()/rollback logic believes a shim is active that
        # no route was ever registered for.
        assert result.shim.deployed is False
        assert loop.get_deployed_shims("test_src") == []
        assert "test_src" not in loop._post_deploy_watch

    @pytest.mark.asyncio
    async def test_successful_callback_still_reports_deployed(self) -> None:
        """Control: when the callback succeeds, behaviour is unchanged."""
        calls = []

        def _ok_callback(source_id, shim_code, recovery_id):
            calls.append((source_id, shim_code, recovery_id))

        loop = _wire_real_loop(_ok_callback)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                loop, "_validate_shim",
                AsyncMock(return_value={"passed": True, "post_kl": 0.01}),
            )
            result = await loop.run(_drift_result(), _batch())

        assert result.status == RecoveryStatus.DEPLOYED
        assert result.shim.deployed is True
        assert loop.get_deployed_shims("test_src") == ["return rows"]
        assert len(calls) == 1

    def test_deploy_approved_shim_reraises_on_callback_failure(self) -> None:
        """S41 human-approval path: approve_recovery() in service.py always
        marks the recovery DEPLOYED right after calling
        deploy_approved_shim() -- the only way that write stays fail-closed
        is if a downstream persistence failure propagates as an exception
        instead of being swallowed."""
        def _failing_callback(source_id, shim_code, recovery_id):
            raise RuntimeError("route registration failed")

        loop = RecoveryLoop(detector=_FakeDetector(), on_shim_deployed=_failing_callback)

        with pytest.raises(RuntimeError, match="route registration failed"):
            loop.deploy_approved_shim("test_src", "return rows", "recovery_042")

        # Rolled back -- the registry must not retain a shim entry for a
        # deployment whose callback never completed.
        assert loop.get_deployed_shims("test_src") == []
