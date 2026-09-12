"""
DSR-007b — canary causal estimate.

Tests the estimator module directly (treatment/outcome/DAG construction,
insufficient-data guard, never-raises contract) and its wiring into
ShimRouter.promote_canary as a purely advisory field.
"""
from __future__ import annotations

import time

import pytest

from uasr.canary_causal_estimator import MIN_SAMPLES_PER_ARM, estimate_canary_effect
from uasr.shim_router import OutcomeRecord, ShimRouter


def _records(version: str, n: int, base_outcome: float, covariates=None) -> list:
    return [
        OutcomeRecord(
            version=version, outcome=base_outcome + i * 0.01,
            timestamp=float(i), covariates=dict(covariates or {"row_count": 100}),
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_returns_none_with_insufficient_data_in_either_arm():
    history = _records("canary", MIN_SAMPLES_PER_ARM - 1, 1.0) + _records("baseline", 20, 1.0)
    assert await estimate_canary_effect(history, "canary") is None


@pytest.mark.asyncio
async def test_returns_none_with_no_history():
    assert await estimate_canary_effect([], "canary") is None


@pytest.mark.asyncio
async def test_produces_a_real_estimate_with_enough_history():
    """Canary consistently has LOWER drift distance than baseline across a
    covariate (row_count) that varies independently of version -- the
    DR-Learner should recover a real, negative point estimate (canary
    reduces drift distance)."""
    canary = [
        OutcomeRecord(version="canary", outcome=0.5, timestamp=float(i),
                       covariates={"row_count": 100.0 + i})
        for i in range(20)
    ]
    baseline = [
        OutcomeRecord(version="baseline", outcome=2.0, timestamp=float(i),
                       covariates={"row_count": 100.0 + i})
        for i in range(20)
    ]
    result = await estimate_canary_effect(canary + baseline, "canary")

    assert result is not None
    assert result.get("error") is None, result
    assert result["n_treated"] == 20
    assert result["n_control"] == 20
    assert result["point"] < 0, (
        f"expected a negative point estimate (canary reduces drift distance), got {result}"
    )


@pytest.mark.asyncio
async def test_never_raises_on_malformed_history(monkeypatch):
    """Advisory signal -- a broken estimate must fail open, not raise and
    take promote_canary's decision down with it."""
    import counterfactual_service.engine as engine_mod

    async def _boom(*_a, **_k):
        raise RuntimeError("dowhy exploded")

    monkeypatch.setattr(engine_mod, "run_estimators", _boom)

    canary = _records("canary", 10, 1.0)
    baseline = _records("baseline", 10, 1.0)
    result = await estimate_canary_effect(canary + baseline, "canary")
    assert result is not None
    assert "error" in result
    assert "dowhy exploded" in result["error"]


# ── Wiring into promote_canary ──────────────────────────────────────────


async def _t(source_id: str, rows):
    return rows


@pytest.mark.asyncio
async def test_promote_canary_attaches_causal_estimate_key():
    """promote_canary's response must always carry a causal_estimate key
    (None when there's not enough history), and it must never change
    `promoted` -- purely advisory."""
    router = ShimRouter()
    await router.add_route("src", "v1", _t)
    await router.add_canary("src", "v2", _t, initial_weight=0.1)

    result = await router.promote_canary("src", "v2", min_samples=999)
    assert "causal_estimate" in result
    assert result["causal_estimate"] is None  # no outcome history recorded yet
    assert result["promoted"] is False  # unaffected by the (missing) causal signal


@pytest.mark.asyncio
async def test_promote_canary_causal_estimate_populated_with_history():
    router = ShimRouter()
    await router.add_route("src", "v1", _t)
    await router.add_canary("src", "v2", _t, initial_weight=0.1)
    for _ in range(3):
        await router.record_canary_score("src", "v2", 0.9)

    now = time.time()
    for i in range(20):
        router.record_outcome("src", "v2", 0.5, now + i, {"row_count": 100.0 + i})
        router.record_outcome("src", "v1", 2.0, now + i, {"row_count": 100.0 + i})

    result = await router.promote_canary("src", "v2", ratio_step=0.2)
    assert result["promoted"] is True  # unchanged decision logic
    assert result["causal_estimate"] is not None
    assert result["causal_estimate"]["n_treated"] == 20
