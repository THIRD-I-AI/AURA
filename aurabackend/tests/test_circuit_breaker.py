"""
AURA Circuit Breaker Tests
===========================
Tests for CircuitBreaker states, transitions, failure counting, and registry.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.circuit_breaker import (
    _REGISTRY,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    ServiceUnavailableError,
    all_breaker_states,
    get_breaker,
)

# ── Helpers ───────────────────────────────────────────────────────────

async def _ok_coro():
    return "ok"


async def _fail_coro():
    raise RuntimeError("boom")


async def _slow_coro():
    await asyncio.sleep(30)
    return "slow"


# ── ServiceUnavailableError ──────────────────────────────────────────

def test_service_unavailable_error():
    err = ServiceUnavailableError("my_svc")
    assert "my_svc" in str(err)
    assert err.service == "my_svc"


# ── CircuitBreaker init ─────────────────────────────────────────────

def test_default_config():
    cb = CircuitBreaker("svc1")
    assert cb.state == CircuitState.CLOSED
    assert cb._cfg.failure_threshold == 5


def test_custom_config():
    cfg = CircuitBreakerConfig(failure_threshold=2, success_threshold=1, open_timeout_seconds=0.1)
    cb = CircuitBreaker("svc2", config=cfg)
    assert cb._cfg.failure_threshold == 2


# ── Successful calls keep state CLOSED ───────────────────────────────

@pytest.mark.asyncio
async def test_successful_call():
    cb = CircuitBreaker("svc_ok")
    result = await cb.call(_ok_coro())
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


# ── Failure counting and transition to OPEN ──────────────────────────

@pytest.mark.asyncio
async def test_transitions_to_open_after_threshold():
    cfg = CircuitBreakerConfig(failure_threshold=3, open_timeout_seconds=60)
    cb = CircuitBreaker("svc_fail", config=cfg)

    for _ in range(3):
        result = await cb.call(_fail_coro(), fallback="fb")
        assert result == "fb"

    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_returns_fallback():
    cfg = CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=60)
    cb = CircuitBreaker("svc_fb", config=cfg)

    # Trip the breaker
    await cb.call(_fail_coro(), fallback="fb")
    assert cb.state == CircuitState.OPEN

    # Subsequent call returns fallback without executing the coro
    result = await cb.call(_ok_coro(), fallback="fast_fail")
    assert result == "fast_fail"


@pytest.mark.asyncio
async def test_open_raises_when_requested():
    cfg = CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=60)
    cb = CircuitBreaker("svc_raise", config=cfg)

    await cb.call(_fail_coro(), fallback="fb")
    assert cb.state == CircuitState.OPEN

    with pytest.raises(ServiceUnavailableError):
        await cb.call(_ok_coro(), raise_on_open=True)


# ── HALF_OPEN transition after timeout ───────────────────────────────

@pytest.mark.asyncio
async def test_transitions_to_half_open_after_timeout():
    cfg = CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=0.05)
    cb = CircuitBreaker("svc_ho", config=cfg)

    await cb.call(_fail_coro(), fallback="fb")
    assert cb._state == CircuitState.OPEN

    # Wait for open_timeout to expire
    await asyncio.sleep(0.1)

    # State should now report HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN


# ── HALF_OPEN → CLOSED on success ───────────────────────────────────

@pytest.mark.asyncio
async def test_half_open_recovers_to_closed():
    cfg = CircuitBreakerConfig(
        failure_threshold=1,
        success_threshold=2,
        open_timeout_seconds=0.05,
    )
    cb = CircuitBreaker("svc_recover", config=cfg)

    await cb.call(_fail_coro(), fallback="fb")
    await asyncio.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN

    # Two successful probes should close the breaker
    await cb.call(_ok_coro())
    await cb.call(_ok_coro())
    assert cb.state == CircuitState.CLOSED


# ── HALF_OPEN → OPEN on failure ─────────────────────────────────────

@pytest.mark.asyncio
async def test_half_open_fails_back_to_open():
    cfg = CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=0.05)
    cb = CircuitBreaker("svc_hofail", config=cfg)

    await cb.call(_fail_coro(), fallback="fb")
    await asyncio.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN

    # Failure in half-open should revert to OPEN
    await cb.call(_fail_coro(), fallback="fb")
    assert cb._state == CircuitState.OPEN


# ── raise_on_open in failure path ────────────────────────────────────

@pytest.mark.asyncio
async def test_raise_on_open_during_failure():
    cfg = CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=60)
    cb = CircuitBreaker("svc_rofail", config=cfg)

    with pytest.raises(ServiceUnavailableError):
        await cb.call(_fail_coro(), raise_on_open=True)


# ── Timeout handling ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_timeout():
    cfg = CircuitBreakerConfig(failure_threshold=1, call_timeout_seconds=0.05)
    cb = CircuitBreaker("svc_timeout", config=cfg)

    result = await cb.call(_slow_coro(), fallback="timed_out")
    assert result == "timed_out"


# ── to_dict ──────────────────────────────────────────────────────────

def test_to_dict():
    cb = CircuitBreaker("svc_dict")
    d = cb.to_dict()
    assert d["service"] == "svc_dict"
    assert d["state"] == "closed"
    assert d["failure_count"] == 0
    assert d["success_count"] == 0
    assert d["opened_at"] is None


# ── Registry functions ───────────────────────────────────────────────

def test_get_breaker_creates_and_caches():
    _REGISTRY.clear()
    b1 = get_breaker("reg_test")
    b2 = get_breaker("reg_test")
    assert b1 is b2
    _REGISTRY.clear()


def test_all_breaker_states():
    _REGISTRY.clear()
    get_breaker("a")
    get_breaker("b")
    states = all_breaker_states()
    assert "a" in states
    assert "b" in states
    assert states["a"]["state"] == "closed"
    _REGISTRY.clear()
