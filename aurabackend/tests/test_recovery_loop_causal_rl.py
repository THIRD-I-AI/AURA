"""
Sprint S18.1b — CausalRLEvaluator wired into RecoveryLoop.

Verifies:
* When ``use_causal_rl_evaluator=False`` (default), the loop deploys
  the first validated shim greedily (existing behaviour, no regression).
* When ``use_causal_rl_evaluator=True``, the loop collects all validated
  candidates and defers deployment to the evaluator's ``select_winner``.
* The ``evaluation_artifact`` field on ``RecoveryLoopResult`` is
  populated when the evaluator runs and ``None`` when it doesn't.
* Fallback: if the evaluator raises, the first validated candidate is
  deployed (graceful degradation).

All tests use a fake DriftDetector + mock agents so no LLM / econml /
dowhy is needed — these run on the base backend CI lane.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional
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


def _shim(code: str = "rows") -> ShimResult:
    return ShimResult(
        shim_code=code,
        shim_type="transform",
        confidence=0.9,
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


class TestGreedyPathUnchanged:

    @pytest.mark.asyncio
    async def test_deploys_first_validated_shim(self) -> None:
        cfg = RecoveryLoopConfig(
            max_iterations=3,
            use_causal_rl_evaluator=False,
        )
        loop = RecoveryLoop(detector=_FakeDetector(), config=cfg)
        loop._reflector = MagicMock()
        loop._reflector.execute = AsyncMock(return_value=MagicMock(
            succeeded=True, artifacts={"diagnosis": MagicMock(model_dump=lambda: {})},
        ))
        loop._actuator = MagicMock()
        loop._actuator.execute = AsyncMock(return_value=MagicMock(
            succeeded=True, artifacts={"shim": _shim("return rows")},
        ))

        with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {"passed": True, "post_kl": 0.01}
            result = await loop.run(_drift_result(), _batch())

        assert result.status == RecoveryStatus.DEPLOYED
        assert result.evaluation_artifact is None

    @pytest.mark.asyncio
    async def test_no_evaluator_when_flag_off(self) -> None:
        cfg = RecoveryLoopConfig(use_causal_rl_evaluator=False)
        loop = RecoveryLoop(detector=_FakeDetector(), config=cfg)
        assert loop._evaluator is None


class TestCausalRLPath:

    @pytest.mark.asyncio
    async def test_collects_candidates_before_deploying(self) -> None:
        cfg = RecoveryLoopConfig(
            max_iterations=3,
            use_causal_rl_evaluator=True,
        )
        loop = RecoveryLoop(detector=_FakeDetector(), config=cfg)
        assert loop._evaluator is not None

        loop._reflector = MagicMock()
        loop._reflector.execute = AsyncMock(return_value=MagicMock(
            succeeded=True, artifacts={"diagnosis": MagicMock(model_dump=lambda: {})},
        ))

        call_count = 0
        async def _generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _shim(f"return rows  # variant {call_count}")
        loop._generate_shim = _generate

        with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {"passed": True, "post_kl": 0.01}

            # Mock the evaluator to pick the second candidate
            from uasr.causal_rl_evaluator import CandidateEvaluation, EvaluationArtifact
            mock_artifact = EvaluationArtifact(
                record_id="eval_001",
                audit_record_hash="abc123",
                source_id="test_src",
                drift_event_id="batch_001",
                candidates=[
                    CandidateEvaluation(
                        candidate_id="candidate_0",
                        drift_score_before=1.0,
                        drift_score_after=0.5,
                        improvement=0.5,
                        ci_lower=0.3,
                        ci_upper=0.7,
                        elapsed_ms=10,
                    ),
                    CandidateEvaluation(
                        candidate_id="candidate_1",
                        drift_score_before=1.0,
                        drift_score_after=0.2,
                        improvement=0.8,
                        ci_lower=0.6,
                        ci_upper=1.0,
                        elapsed_ms=12,
                    ),
                ],
                winner_id="candidate_1",
                selection_rationale="highest improvement",
                timestamp_iso="2026-01-01T00:00:00Z",
            )
            loop._evaluator.select_winner = AsyncMock(return_value=mock_artifact)

            result = await loop.run(_drift_result(), _batch())

        assert result.status == RecoveryStatus.DEPLOYED
        assert result.evaluation_artifact is not None
        assert result.evaluation_artifact["winner_id"] == "candidate_1"
        assert "return rows  # variant 2" in result.shim.shim_code

    @pytest.mark.asyncio
    async def test_evaluator_failure_falls_back_to_first(self) -> None:
        cfg = RecoveryLoopConfig(
            max_iterations=2,
            use_causal_rl_evaluator=True,
        )
        loop = RecoveryLoop(detector=_FakeDetector(), config=cfg)

        loop._reflector = MagicMock()
        loop._reflector.execute = AsyncMock(return_value=MagicMock(
            succeeded=True, artifacts={"diagnosis": MagicMock(model_dump=lambda: {})},
        ))

        async def _generate(*args, **kwargs):
            return _shim("return rows  # fallback")
        loop._generate_shim = _generate

        with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {"passed": True, "post_kl": 0.02}
            loop._evaluator.select_winner = AsyncMock(
                side_effect=RuntimeError("evaluator crashed"),
            )
            result = await loop.run(_drift_result(), _batch())

        assert result.status == RecoveryStatus.DEPLOYED
        assert "fallback" in result.shim.shim_code

    @pytest.mark.asyncio
    async def test_evaluator_not_constructed_when_flag_off(self) -> None:
        cfg = RecoveryLoopConfig(use_causal_rl_evaluator=False)
        loop = RecoveryLoop(detector=_FakeDetector(), config=cfg)
        assert loop._evaluator is None
