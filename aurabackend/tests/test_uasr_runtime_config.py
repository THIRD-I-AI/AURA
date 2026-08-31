"""Tests for the UASR deployment-mode factory (runtime_config).

These lock the operational contract: the same image becomes single-node or a
distributed fleet by environment variables alone.  Redis-backed modes use
``fakeredis`` (importorskip) so no server is required.
"""
from __future__ import annotations

import importlib

import pytest

rc = importlib.import_module("uasr.runtime_config")
from uasr.repair_scheduler import RepairScheduler  # noqa: E402
from uasr.state_store import InMemoryStateStore, RedisStateStore  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_uasr_env(monkeypatch):
    """Every test starts from a clean env — no UASR_* leakage across cases."""
    for k in list(__import__("os").environ):
        if k.startswith("UASR_"):
            monkeypatch.delenv(k, raising=False)
    yield


# ── state backend ────────────────────────────────────────────────────

def test_default_state_is_unbounded_memory():
    ss = rc.build_state_store()
    assert isinstance(ss, InMemoryStateStore)
    assert ss._capacity is None


def test_memory_state_capacity_bounds_lru(monkeypatch):
    monkeypatch.setenv("UASR_STATE_BACKEND", "memory")
    monkeypatch.setenv("UASR_STATE_CAPACITY", "500")
    ss = rc.build_state_store()
    assert isinstance(ss, InMemoryStateStore)
    assert ss._capacity == 500


def test_redis_state_backend(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    monkeypatch.setenv("UASR_STATE_BACKEND", "redis")
    ss = rc.build_state_store(redis_client=fakeredis.FakeStrictRedis())
    assert isinstance(ss, RedisStateStore)


def test_unknown_state_backend_raises(monkeypatch):
    monkeypatch.setenv("UASR_STATE_BACKEND", "cassandra")
    with pytest.raises(ValueError, match="UASR_STATE_BACKEND"):
        rc.build_state_store()


# ── repair backend ───────────────────────────────────────────────────

def test_default_repair_is_local_scheduler():
    r = rc.build_repair_scheduler()
    assert isinstance(r, RepairScheduler)
    assert r._max_concurrent == 4


def test_repair_max_concurrent_env(monkeypatch):
    monkeypatch.setenv("UASR_REPAIR_MAX_CONCURRENT", "16")
    r = rc.build_repair_scheduler()
    assert isinstance(r, RepairScheduler)
    assert r._max_concurrent == 16


def test_repair_none_disables(monkeypatch):
    monkeypatch.setenv("UASR_REPAIR_BACKEND", "none")
    assert rc.build_repair_scheduler() is None


def test_repair_max_per_source_off_by_default():
    r = rc.build_repair_scheduler()
    assert isinstance(r, RepairScheduler)
    assert r._max_per_source == 0


def test_repair_max_per_source_env(monkeypatch):
    monkeypatch.setenv("UASR_REPAIR_MAX_PER_SOURCE", "2")
    r = rc.build_repair_scheduler()
    assert isinstance(r, RepairScheduler)
    assert r._max_per_source == 2


def test_distributed_repair_backend(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    from uasr.distributed_repair import DistributedRepairCoordinator
    monkeypatch.setenv("UASR_REPAIR_BACKEND", "distributed")
    monkeypatch.setenv("UASR_REPAIR_MAX_GLOBAL_CONCURRENT", "8")
    r = rc.build_repair_scheduler(redis_client=fakeredis.FakeStrictRedis())
    assert isinstance(r, DistributedRepairCoordinator)
    assert r._max == 8
    assert r._max_per_source == 0  # off by default, same posture as local


def test_distributed_repair_max_per_source_env(monkeypatch):
    """The fleet-wide per-source cap shares UASR_REPAIR_MAX_PER_SOURCE with
    the local backend -- one knob, same meaning, regardless of which
    admission backend is active."""
    fakeredis = pytest.importorskip("fakeredis")
    from uasr.distributed_repair import DistributedRepairCoordinator
    monkeypatch.setenv("UASR_REPAIR_BACKEND", "distributed")
    monkeypatch.setenv("UASR_REPAIR_MAX_PER_SOURCE", "3")
    r = rc.build_repair_scheduler(redis_client=fakeredis.FakeStrictRedis())
    assert isinstance(r, DistributedRepairCoordinator)
    assert r._max_per_source == 3


def test_unknown_repair_backend_raises(monkeypatch):
    monkeypatch.setenv("UASR_REPAIR_BACKEND", "sidecar")
    with pytest.raises(ValueError, match="UASR_REPAIR_BACKEND"):
        rc.build_repair_scheduler()


# ── deployment summary ───────────────────────────────────────────────

def test_deployment_summary_default():
    s = rc.deployment_summary()
    assert s["state_backend"] == "memory"
    assert s["repair_backend"] == "local"
    assert s["repair_max_concurrent"] == 4
    assert s["repair_max_per_source"] == 0
    assert "redis_url" not in s  # no redis dependency surfaced in default mode


def test_deployment_summary_distributed(monkeypatch):
    monkeypatch.setenv("UASR_STATE_BACKEND", "redis")
    monkeypatch.setenv("UASR_REPAIR_BACKEND", "distributed")
    monkeypatch.setenv("UASR_NODE_ID", "node-7")
    s = rc.deployment_summary()
    assert s["state_backend"] == "redis"
    assert s["repair_backend"] == "distributed"
    assert s["redis_url"].startswith("redis://")
    assert s["node_id"] == "node-7"
    assert s["repair_max_global_concurrent"] == 8
    assert s["repair_max_per_source"] == 0


# ── numeric value-healing flags ──────────────────────────────────────

def test_numeric_heal_off_by_default():
    """Value-healing rewrites pipeline data, so it must stay opt-in."""
    assert rc.numeric_heal_flags() == (False, False)
    s = rc.deployment_summary()
    assert s["numeric_semantics"] is False
    assert s["numeric_auto_heal"] is False


def test_numeric_semantics_alone_analyses_without_rewriting(monkeypatch):
    monkeypatch.setenv("UASR_NUMERIC_SEMANTICS", "true")
    assert rc.numeric_heal_flags() == (True, False)


def test_auto_heal_alone_still_enables_the_semantics_channel(monkeypatch):
    """AUTO_HEAL without SEMANTICS would build a healer with no analyzer.

    MAPEKWorker only constructs the NumericSemanticAnalyzer when
    use_numeric_semantics is on, and the healer shares that analyzer's
    baselines -- so the half-configured combination heals nothing and raises
    nothing. The resolver promotes it instead of failing silently.
    """
    monkeypatch.setenv("UASR_NUMERIC_AUTO_HEAL", "true")
    assert rc.numeric_heal_flags() == (True, True)


def test_numeric_flags_reach_the_mapek_config(monkeypatch):
    """The reachability gap this closes: the worker was built with a bare
    MAPEKConfig(), whose numeric fields default False, so NumericHealController
    -- the only component that repairs a drifted VALUE instead of reporting it
    -- could not be switched on in any deployment."""
    from uasr.service import _mapek_config

    assert _mapek_config().numeric_auto_heal is False
    monkeypatch.setenv("UASR_NUMERIC_AUTO_HEAL", "1")
    cfg = _mapek_config()
    assert cfg.use_numeric_semantics is True
    assert cfg.numeric_auto_heal is True


# ── Sprint S18.1 reachability (martingale / shim router / causal-RL) ──

def test_s18_1_flags_off_by_default():
    assert rc.s18_1_flags() == (False, False, False)
    s = rc.deployment_summary()
    assert s["martingale_detector"] is False
    assert s["shim_router"] is False
    assert s["causal_rl_evaluator"] is False


def test_s18_1_flags_resolve_independently(monkeypatch):
    """Unlike numeric-heal, these three detectors share no baseline, so
    enabling one must not imply the others."""
    monkeypatch.setenv("UASR_USE_MARTINGALE_DETECTOR", "true")
    assert rc.s18_1_flags() == (True, False, False)


def test_martingale_and_shim_router_reach_the_mapek_config(monkeypatch):
    """Same reachability gap the numeric-heal fix closed: both flags shipped
    tested in mapek_worker.py but the bare MAPEKConfig() built in service.py
    never set them, so neither detector could be reached from a deployment."""
    from uasr.service import _mapek_config

    assert _mapek_config().use_martingale_detector is False
    assert _mapek_config().use_shim_router is False
    monkeypatch.setenv("UASR_USE_MARTINGALE_DETECTOR", "1")
    monkeypatch.setenv("UASR_USE_SHIM_ROUTER", "1")
    cfg = _mapek_config()
    assert cfg.use_martingale_detector is True
    assert cfg.use_shim_router is True


def test_causal_rl_evaluator_reaches_the_recovery_loop_config(monkeypatch):
    """RecoveryLoopConfig.use_causal_rl_evaluator is resolved once at
    uasr.service import time (same pattern as UASR_RISK_TIERED/UASR_RECOVERY_MODE
    on that module) -- reload the module under the flag to prove the wiring,
    since the module-level ``_loop`` singleton won't re-read the environment
    on its own."""
    monkeypatch.setenv("UASR_USE_CAUSAL_RL_EVALUATOR", "true")
    service = importlib.import_module("uasr.service")
    importlib.reload(service)
    try:
        assert service._loop._config.use_causal_rl_evaluator is True
    finally:
        monkeypatch.delenv("UASR_USE_CAUSAL_RL_EVALUATOR", raising=False)
        importlib.reload(service)
        assert service._loop._config.use_causal_rl_evaluator is False


# ── post-heal validation / auto-rollback ───────────────────────────────

def test_post_heal_validation_off_by_default():
    assert rc.post_heal_validation_batches() == 0
    assert rc.deployment_summary()["post_heal_validation_batches"] == 0


def test_post_heal_validation_resolves_from_env(monkeypatch):
    monkeypatch.setenv("UASR_POST_HEAL_VALIDATION_BATCHES", "5")
    assert rc.post_heal_validation_batches() == 5


def test_post_heal_validation_reaches_the_recovery_loop_config(monkeypatch):
    """Same module-level, env-resolved-at-import pattern as
    UASR_USE_CAUSAL_RL_EVALUATOR above."""
    monkeypatch.setenv("UASR_POST_HEAL_VALIDATION_BATCHES", "3")
    service = importlib.import_module("uasr.service")
    importlib.reload(service)
    try:
        assert service._loop._config.post_heal_validation_batches == 3
    finally:
        monkeypatch.delenv("UASR_POST_HEAL_VALIDATION_BATCHES", raising=False)
        importlib.reload(service)
        assert service._loop._config.post_heal_validation_batches == 0


# ── approval-queue timeout / escalation ────────────────────────────────

def test_approval_timeout_off_by_default():
    assert rc.approval_timeout_seconds() == 0
    assert rc.deployment_summary()["approval_timeout_seconds"] == 0


def test_approval_timeout_resolves_from_env(monkeypatch):
    monkeypatch.setenv("UASR_APPROVAL_TIMEOUT_SECONDS", "3600")
    assert rc.approval_timeout_seconds() == 3600


# ── cross-source drift correlation (candidate #5) ──────────────────────

def test_correlation_off_by_default():
    assert rc.correlation_flags() == (0.0, 3, False)
    s = rc.deployment_summary()
    assert s["correlation_window_seconds"] == 0.0
    assert s["correlation_min_sources"] == 3
    assert s["correlation_auto_heal"] is False


def test_correlation_window_resolves_from_env(monkeypatch):
    monkeypatch.setenv("UASR_CORRELATION_WINDOW_SECONDS", "45")
    monkeypatch.setenv("UASR_CORRELATION_MIN_SOURCES", "5")
    assert rc.correlation_flags() == (45.0, 5, False)


def test_auto_heal_alone_promotes_a_default_window(monkeypatch):
    """UASR_CORRELATION_AUTO_HEAL with no window configured would build a
    detector that never fires and a heal path nothing reaches -- same
    reachability principle as numeric_heal_flags()."""
    monkeypatch.setenv("UASR_CORRELATION_AUTO_HEAL", "true")
    window, min_sources, auto_heal = rc.correlation_flags()
    assert auto_heal is True
    assert window == 30.0


def test_auto_heal_does_not_override_an_explicit_window(monkeypatch):
    monkeypatch.setenv("UASR_CORRELATION_AUTO_HEAL", "true")
    monkeypatch.setenv("UASR_CORRELATION_WINDOW_SECONDS", "120")
    assert rc.correlation_flags() == (120.0, 3, True)
