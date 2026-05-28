"""
Sprint S32 — UASR Shim Router (Kramer-Magee canary) tests.

Tier A (pure Python, no optional deps).

Covers:
  * add_route: basic, duplicate rejection, weight validation
  * add_canary: weight rescaling, duplicate rejection
  * apply: routing to registered transform, pass-through when no routes
  * promote_canary: score-gated promotion, insufficient samples
  * revert_canary: weight zeroed, others rescaled
  * drain_to_quiescence: marks for drain, returns True when no in-flight
  * remove_route: drops from table
  * routes: snapshot for audit endpoint
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.shim_router import ShimRouter


def _identity_transform(source_id, rows):
    return rows


def _double_transform(source_id, rows):
    return [
        {k: v * 2 if isinstance(v, (int, float)) else v for k, v in row.items()}
        for row in rows
    ]


# ── add_route ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAddRoute:
    async def test_basic(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        snapshot = router.routes("src")
        assert len(snapshot) == 1
        assert snapshot[0]["version"] == "v1"
        assert snapshot[0]["weight"] == 1.0

    async def test_duplicate_rejected(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        with pytest.raises(ValueError, match="already exists"):
            await router.add_route("src", "v1", _identity_transform)

    async def test_invalid_weight(self):
        router = ShimRouter()
        with pytest.raises(ValueError, match="weight"):
            await router.add_route("src", "v1", _identity_transform, weight=1.5)


# ── add_canary ────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAddCanary:
    async def test_rescales_existing_weights(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform, weight=1.0)
        await router.add_canary("src", "v2", _double_transform, initial_weight=0.1)
        snapshot = router.routes("src")
        weights = {r["version"]: r["weight"] for r in snapshot}
        assert abs(weights["v1"] - 0.9) < 1e-10
        assert abs(weights["v2"] - 0.1) < 1e-10

    async def test_duplicate_canary_rejected(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        await router.add_canary("src", "v2", _double_transform)
        with pytest.raises(ValueError, match="already exists"):
            await router.add_canary("src", "v2", _identity_transform)

    async def test_invalid_canary_weight(self):
        router = ShimRouter()
        with pytest.raises(ValueError):
            await router.add_canary("src", "v1", _identity_transform, initial_weight=0.0)
        with pytest.raises(ValueError):
            await router.add_canary("src", "v1", _identity_transform, initial_weight=1.0)


# ── apply ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestApply:
    async def test_routes_to_registered_transform(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        result = await router.apply("src", [{"x": 1}])
        assert result["version"] == "v1"
        assert result["rows"] == [{"x": 1}]

    async def test_passthrough_when_no_routes(self):
        router = ShimRouter()
        result = await router.apply("unknown", [{"x": 1}])
        assert result["version"] == "_passthrough"
        assert result["rows"] == [{"x": 1}]

    async def test_tracks_total_calls(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        for _ in range(5):
            await router.apply("src", [{"x": 1}])
        snapshot = router.routes("src")
        assert snapshot[0]["total_calls"] == 5


# ── promote_canary ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPromoteCanary:
    async def test_promotion_on_good_scores(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        await router.add_canary("src", "v2", _double_transform, initial_weight=0.1)
        for _ in range(3):
            await router.record_canary_score("src", "v2", 0.9)
        result = await router.promote_canary("src", "v2", ratio_step=0.2)
        assert result["promoted"] is True
        assert result["new_weight"] > 0.1

    async def test_insufficient_samples(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        await router.add_canary("src", "v2", _double_transform)
        await router.record_canary_score("src", "v2", 0.9)
        result = await router.promote_canary("src", "v2", min_samples=3)
        assert result["promoted"] is False
        assert "need" in result["reason"]

    async def test_low_score_blocks_promotion(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        await router.add_canary("src", "v2", _double_transform)
        for _ in range(3):
            await router.record_canary_score("src", "v2", 0.3)
        result = await router.promote_canary("src", "v2", min_avg_score=0.6)
        assert result["promoted"] is False
        assert "below" in result["reason"]

    async def test_unknown_route(self):
        router = ShimRouter()
        result = await router.promote_canary("src", "vX")
        assert result["promoted"] is False
        assert "unknown" in result["reason"]


# ── revert_canary ─────────────────────────────────────────────────

@pytest.mark.asyncio
class TestRevertCanary:
    async def test_revert_zeros_weight_and_rescales(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        await router.add_canary("src", "v2", _double_transform, initial_weight=0.2)
        await router.revert_canary("src", "v2")
        snapshot = router.routes("src")
        weights = {r["version"]: r["weight"] for r in snapshot}
        assert weights["v2"] == 0.0
        assert abs(weights["v1"] - 1.0) < 1e-10

    async def test_revert_nonexistent_is_noop(self):
        router = ShimRouter()
        await router.revert_canary("src", "vX")


# ── drain_to_quiescence ──────────────────────────────────────────

@pytest.mark.asyncio
class TestDrainToQuiescence:
    async def test_drain_immediately_when_no_inflight(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        drained = await router.drain_to_quiescence("src", "v1", timeout_s=1.0)
        assert drained is True
        snapshot = router.routes("src")
        assert snapshot[0]["marked_for_drain"] is True

    async def test_drain_nonexistent_returns_true(self):
        router = ShimRouter()
        assert await router.drain_to_quiescence("src", "vX") is True


# ── remove_route ──────────────────────────────────────────────────

@pytest.mark.asyncio
class TestRemoveRoute:
    async def test_remove_drops_from_table(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        await router.remove_route("src", "v1")
        assert router.routes("src") == []

    async def test_remove_nonexistent_is_noop(self):
        router = ShimRouter()
        await router.remove_route("src", "vX")


# ── routes snapshot ───────────────────────────────────────────────

@pytest.mark.asyncio
class TestRoutesSnapshot:
    async def test_snapshot_fields(self):
        router = ShimRouter()
        await router.add_route("src", "v1", _identity_transform)
        await router.apply("src", [{"x": 1}])
        snapshot = router.routes("src")
        assert len(snapshot) == 1
        r = snapshot[0]
        assert "version" in r
        assert "weight" in r
        assert "in_flight" in r
        assert "total_calls" in r
        assert "deployed_at" in r
        assert r["total_calls"] == 1

    async def test_empty_source(self):
        router = ShimRouter()
        assert router.routes("nonexistent") == []
