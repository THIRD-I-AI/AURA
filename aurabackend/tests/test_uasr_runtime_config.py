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


def test_distributed_repair_backend(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    from uasr.distributed_repair import DistributedRepairCoordinator
    monkeypatch.setenv("UASR_REPAIR_BACKEND", "distributed")
    monkeypatch.setenv("UASR_REPAIR_MAX_GLOBAL_CONCURRENT", "8")
    r = rc.build_repair_scheduler(redis_client=fakeredis.FakeStrictRedis())
    assert isinstance(r, DistributedRepairCoordinator)
    assert r._max == 8


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
