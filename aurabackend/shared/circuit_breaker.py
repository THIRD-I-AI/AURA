"""
Circuit Breaker
================
Protects outbound calls from the API Gateway to downstream microservices.

States:
  CLOSED    — normal operation, calls pass through
  OPEN      — failing, fast-fail immediately (raises ServiceUnavailableError)
  HALF_OPEN — probe mode, one test call allowed; success resets to CLOSED,
              failure resets to OPEN

Usage::

    breaker = get_breaker("connector_service")
    result = await breaker.call(client.get("http://..."), fallback={})
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Coroutine, Dict, Optional

logger = logging.getLogger("aura.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class ServiceUnavailableError(Exception):
    """Raised when a circuit breaker is OPEN."""
    def __init__(self, service: str) -> None:
        super().__init__(f"Service '{service}' is currently unavailable (circuit OPEN)")
        self.service = service


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int   = 5     # consecutive failures before OPEN
    success_threshold: int   = 2     # consecutive successes to close from HALF_OPEN
    open_timeout_seconds: float = 60.0  # how long to stay OPEN before probing
    call_timeout_seconds: float = 10.0  # per-call timeout


class CircuitBreaker:
    """Single circuit breaker for one downstream service."""

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> None:
        self._name = name
        self._cfg = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: Optional[float] = None
        self._lock = asyncio.Lock()

    # ── Public API ─────────────────────────────────────────────────

    async def call(
        self,
        coro: Coroutine,
        fallback: Any = None,
        raise_on_open: bool = False,
    ) -> Any:
        """
        Execute `coro` through the breaker.

        If the breaker is OPEN:
          - `raise_on_open=True`  → raises ServiceUnavailableError
          - `raise_on_open=False` → returns `fallback` immediately

        Args:
            coro: An awaitable (e.g. `httpx_client.get(url)`)
            fallback: Value to return when fast-failing
            raise_on_open: If True, raises instead of returning fallback
        """
        async with self._lock:
            state = self._get_effective_state()

            if state == CircuitState.OPEN:
                if raise_on_open:
                    raise ServiceUnavailableError(self._name)
                logger.warning("Circuit OPEN for '%s' — fast failing", self._name)
                return fallback

            if state == CircuitState.HALF_OPEN:
                logger.info("Circuit HALF_OPEN for '%s' — sending probe", self._name)

        try:
            result = await asyncio.wait_for(coro, timeout=self._cfg.call_timeout_seconds)
            async with self._lock:
                self._on_success()
            return result

        except (asyncio.TimeoutError, Exception) as exc:
            async with self._lock:
                self._on_failure(exc)
            if raise_on_open and self._state == CircuitState.OPEN:
                raise ServiceUnavailableError(self._name) from exc
            logger.error("Circuit breaker '%s' call failed: %s", self._name, exc)
            return fallback

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self._name,
            "state": self._get_effective_state().value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "opened_at": self._opened_at,
        }

    @property
    def state(self) -> CircuitState:
        return self._get_effective_state()

    # ── Internal transitions ────────────────────────────────────────

    def _get_effective_state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._cfg.open_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info("Circuit '%s' → HALF_OPEN (probe window)", self._name)
        return self._state

    def _on_success(self) -> None:
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._cfg.success_threshold:
                self._state = CircuitState.CLOSED
                self._opened_at = None
                logger.info("Circuit '%s' → CLOSED (recovered)", self._name)
        # Already CLOSED — stay closed

    def _on_failure(self, exc: Exception) -> None:
        self._success_count = 0
        self._failure_count += 1
        if self._state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            if (self._failure_count >= self._cfg.failure_threshold
                    or self._state == CircuitState.HALF_OPEN):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.error(
                    "Circuit '%s' → OPEN after %d failures (last: %s)",
                    self._name, self._failure_count, exc,
                )


# ── Registry ───────────────────────────────────────────────────────

_REGISTRY: Dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = asyncio.Lock()


def get_breaker(service_name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
    """Return (or lazily create) the circuit breaker for a named service."""
    if service_name not in _REGISTRY:
        _REGISTRY[service_name] = CircuitBreaker(service_name, config)
    return _REGISTRY[service_name]


def all_breaker_states() -> Dict[str, Dict[str, Any]]:
    """Snapshot of all circuit breakers — used by /system/health."""
    return {name: breaker.to_dict() for name, breaker in _REGISTRY.items()}
