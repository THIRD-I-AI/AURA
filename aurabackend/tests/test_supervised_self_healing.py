"""
Sprint S41 — Supervised self-healing: risk-tiered human-in-the-loop approval.

Verifies the risk gate that decides whether a validated shim auto-deploys or
is held in PENDING_APPROVAL for a human:

* New model surface exists (RecoveryMode, PENDING_APPROVAL / APPROVED /
  REJECTED / ESCALATED, ShimResult.generation_method).
* No regression: risk_tiered=False (default) preserves the greedy deploy.
* risk_tiered=True, AUTO mode: deterministic template shim at low/medium
  severity → auto-deploys.
* risk_tiered=True, AUTO mode: LLM/fallback shim, OR high/critical severity →
  held in PENDING_APPROVAL (fail-closed, not deployed).
* SUPERVISED / MONITOR_ONLY modes always hold, even for a safe template shim.

Pure-Python: fake detector + mock agents, no LLM / econml / dowhy needed —
runs on the base backend CI lane.
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
    RecoveryMode,
    RecoveryStatus,
    ShimResult,
)
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig


def _drift(severity: str = "low") -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="src",
        batch_id="b1",
        drift_detected=True,
        drift_type=DriftType.SCHEMA,
        severity=severity,
        details="column renamed",
        drift_vector={"col": "old"},
    )


def _batch() -> BatchPayload:
    return BatchPayload(source_id="src", batch_id="b1", rows=[{"a": 1}, {"a": 2}])


def _shim(method: str = "template") -> ShimResult:
    return ShimResult(recovery_id="r1", shim_code="return rows", generation_method=method)


class _FakeDetector:
    def detect(self, batch):
        return DriftDetectionResult(
            source_id=batch.source_id,
            batch_id=batch.batch_id,
            drift_detected=False,
            drift_type=DriftType.STATISTICAL,
            severity="low",
            details="",
            drift_vector={},
            kl_divergence=0.01,
        )


def _loop(cfg: RecoveryLoopConfig, shim: ShimResult) -> RecoveryLoop:
    loop = RecoveryLoop(detector=_FakeDetector(), config=cfg)
    loop._reflector = MagicMock()
    loop._reflector.execute = AsyncMock(return_value=MagicMock(
        succeeded=True, artifacts={"diagnosis": MagicMock(model_dump=lambda: {})},
    ))
    loop._actuator = MagicMock()
    loop._actuator.execute = AsyncMock(return_value=MagicMock(
        succeeded=True, artifacts={"shim": shim},
    ))
    return loop


async def _run(cfg: RecoveryLoopConfig, shim: ShimResult, drift: DriftDetectionResult):
    loop = _loop(cfg, shim)
    with patch.object(loop, "_validate_shim", new_callable=AsyncMock) as mock_val:
        mock_val.return_value = {"passed": True, "post_kl": 0.01}
        return await loop.run(drift, _batch())


# ── Model surface ──────────────────────────────────────────────────

def test_s41_model_surface() -> None:
    assert RecoveryStatus.PENDING_APPROVAL.value == "pending_approval"
    assert RecoveryStatus.APPROVED.value == "approved"
    assert RecoveryStatus.REJECTED.value == "rejected"
    assert RecoveryStatus.ESCALATED.value == "escalated"
    assert {m.value for m in RecoveryMode} == {"auto", "supervised", "monitor_only"}
    assert ShimResult(recovery_id="r").generation_method == "template"


# ── No regression: risk_tiered off (pre-S41 greedy behavior) ───────

@pytest.mark.asyncio
async def test_no_regression_greedy_deploy_when_risk_tiered_off() -> None:
    # Even an LLM shim at HIGH severity deploys when risk_tiered is off.
    cfg = RecoveryLoopConfig(risk_tiered=False, auto_deploy=True)
    result = await _run(cfg, _shim("llm"), _drift("high"))
    assert result.status == RecoveryStatus.DEPLOYED


# ── Risk-tiered AUTO mode ──────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["low", "medium"])
async def test_template_lowmed_auto_deploys(severity: str) -> None:
    cfg = RecoveryLoopConfig(risk_tiered=True, mode=RecoveryMode.AUTO)
    result = await _run(cfg, _shim("template"), _drift(severity))
    assert result.status == RecoveryStatus.DEPLOYED


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["llm", "fallback"])
async def test_nontemplate_shim_held(method: str) -> None:
    cfg = RecoveryLoopConfig(risk_tiered=True, mode=RecoveryMode.AUTO)
    result = await _run(cfg, _shim(method), _drift("low"))
    assert result.status == RecoveryStatus.PENDING_APPROVAL
    assert result.shim is not None and not result.shim.deployed


@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["high", "critical"])
async def test_high_severity_held_even_for_template(severity: str) -> None:
    cfg = RecoveryLoopConfig(risk_tiered=True, mode=RecoveryMode.AUTO)
    result = await _run(cfg, _shim("template"), _drift(severity))
    assert result.status == RecoveryStatus.PENDING_APPROVAL


# ── Supervised / monitor-only modes ────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [RecoveryMode.SUPERVISED, RecoveryMode.MONITOR_ONLY])
async def test_supervised_modes_always_hold(mode: RecoveryMode) -> None:
    # Even a safe template + low-severity shim waits for a human here.
    cfg = RecoveryLoopConfig(risk_tiered=True, mode=mode)
    result = await _run(cfg, _shim("template"), _drift("low"))
    assert result.status == RecoveryStatus.PENDING_APPROVAL
