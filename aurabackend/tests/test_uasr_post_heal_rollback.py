"""
Post-heal validation + auto-rollback (candidate #2 of the UASR
self-healing gap analysis, docs/superpowers/specs/2026-08-30-uasr-effective-
self-healing-gap-analysis.md).

Before this: a deployed shim was never re-checked against the drift it was
meant to fix. If it made things worse, nothing reverted it automatically.

Verifies:
* ``post_heal_validation_batches=0`` (default) never starts a watch —
  no regression to the pre-existing greedy-deploy behaviour.
* A watch clears the moment the tracked drift_type stops firing (heal
  worked) without touching the deployed shim.
* A watch that's still firing the SAME drift_type after N batches
  auto-reverts the shim via the same in-memory path ``/uasr/rollback`` uses.
* A different drift_type firing doesn't count against the watch (it isn't
  evidence the heal failed).
* ``_deploy_shim`` only starts a watch when the flag is on.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.models import (
    BatchPayload,
    DriftDetectionResult,
    DriftType,
    RecoveryStatus,
    ShimResult,
)
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig


def _drift(detected: bool, drift_type: DriftType = DriftType.SCHEMA) -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="test_src",
        batch_id="batch_001",
        drift_detected=detected,
        drift_type=drift_type,
        severity="high",
    )


def _batch() -> BatchPayload:
    return BatchPayload(
        source_id="test_src", batch_id="batch_001", rows=[{"a": 1}],
    )


def _shim(code: str = "return rows") -> ShimResult:
    return ShimResult(recovery_id="test_recovery_001", shim_code=code)


class _FakeDetector:
    def detect(self, batch):
        return _drift(detected=False)


def _loop(post_heal_validation_batches: int = 0) -> RecoveryLoop:
    cfg = RecoveryLoopConfig(post_heal_validation_batches=post_heal_validation_batches)
    return RecoveryLoop(detector=_FakeDetector(), config=cfg)


class TestCheckPostDeployStateMachine:

    def test_no_watch_is_a_noop(self) -> None:
        loop = _loop(post_heal_validation_batches=3)
        assert loop.check_post_deploy("unwatched_source", _drift(True)) is False

    def test_heal_clears_the_watch(self) -> None:
        loop = _loop(post_heal_validation_batches=3)
        loop._post_deploy_watch["test_src"] = {"drift_type": DriftType.SCHEMA, "batches_seen": 1}
        rolled_back = loop.check_post_deploy("test_src", _drift(False))
        assert rolled_back is False
        assert "test_src" not in loop._post_deploy_watch

    def test_different_drift_type_counts_as_healed(self) -> None:
        """A new, unrelated drift firing isn't evidence the ORIGINAL heal failed."""
        loop = _loop(post_heal_validation_batches=3)
        loop._post_deploy_watch["test_src"] = {"drift_type": DriftType.SCHEMA, "batches_seen": 1}
        rolled_back = loop.check_post_deploy("test_src", _drift(True, DriftType.STATISTICAL))
        assert rolled_back is False
        assert "test_src" not in loop._post_deploy_watch

    def test_sustained_drift_under_threshold_does_not_rollback(self) -> None:
        loop = _loop(post_heal_validation_batches=3)
        loop._deployed_shims["test_src"] = ["return rows"]
        loop._post_deploy_watch["test_src"] = {"drift_type": DriftType.SCHEMA, "batches_seen": 0}
        assert loop.check_post_deploy("test_src", _drift(True)) is False
        assert loop.check_post_deploy("test_src", _drift(True)) is False
        assert loop._post_deploy_watch["test_src"]["batches_seen"] == 2
        assert loop._deployed_shims["test_src"] == ["return rows"]

    def test_sustained_drift_at_threshold_auto_rolls_back(self) -> None:
        loop = _loop(post_heal_validation_batches=3)
        loop._deployed_shims["test_src"] = ["return rows"]
        loop._post_deploy_watch["test_src"] = {"drift_type": DriftType.SCHEMA, "batches_seen": 0}
        assert loop.check_post_deploy("test_src", _drift(True)) is False
        assert loop.check_post_deploy("test_src", _drift(True)) is False
        rolled_back = loop.check_post_deploy("test_src", _drift(True))
        assert rolled_back is True
        assert "test_src" not in loop._post_deploy_watch
        assert loop._deployed_shims["test_src"] == []


class TestDeployStartsTheWatchOnlyWhenOptedIn:

    async def _run_deploy(self, cfg: RecoveryLoopConfig) -> RecoveryLoop:
        loop = RecoveryLoop(detector=_FakeDetector(), config=cfg)
        loop._reflector = MagicMock()
        loop._reflector.execute = AsyncMock(return_value=MagicMock(
            succeeded=True, artifacts={"diagnosis": MagicMock(model_dump=lambda: {})},
        ))
        loop._actuator = MagicMock()
        loop._actuator.execute = AsyncMock(return_value=MagicMock(
            succeeded=True, artifacts={"shim": _shim()},
        ))
        with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {"passed": True, "post_kl": 0.01}
            result = await loop.run(_drift(True), _batch())
        assert result.status == RecoveryStatus.DEPLOYED
        return loop

    @pytest.mark.asyncio
    async def test_flag_off_starts_no_watch(self) -> None:
        loop = await self._run_deploy(RecoveryLoopConfig(post_heal_validation_batches=0))
        assert loop._post_deploy_watch == {}

    @pytest.mark.asyncio
    async def test_flag_on_starts_a_watch_for_the_deployed_drift_type(self) -> None:
        loop = await self._run_deploy(RecoveryLoopConfig(post_heal_validation_batches=3))
        assert loop._post_deploy_watch["test_src"] == {
            "drift_type": DriftType.SCHEMA, "batches_seen": 0,
        }
