"""UASR runtime configuration — deployment-mode factory.

This module is the single place that turns *environment configuration* into the
concrete state / coordination objects the service runs with.  It is what makes
horizontal scale-out an operational toggle rather than a code change: the same
image runs single-node or as a fleet depending on env vars alone.

Two axes, each independently switchable:

**State backend** (``UASR_STATE_BACKEND``)
  * ``memory`` (default) — :class:`InMemoryStateStore`.  Optionally bounded via
    ``UASR_STATE_CAPACITY`` (LRU eviction) for a fixed memory ceiling under many
    sources; unbounded and bit-identical to the legacy detector when unset.
  * ``redis`` — :class:`RedisStateStore` at ``UASR_REDIS_URL``.  Detector state
    lives in Redis, so any replica can serve any source and a cold replica sees
    baselines written by its peers (the cross-replica cold-miss fix).

**Repair admission** (``UASR_REPAIR_BACKEND``)
  * ``local`` (default) — in-process :class:`RepairScheduler`: bounds concurrent
    recoveries *within this process* and admits them in severity priority.
    Correct for a single node or a vertically-scaled (many-worker) box.
  * ``distributed`` — :class:`DistributedRepairCoordinator` over Redis: bounds
    concurrent recoveries *across the whole fleet*, so the shared synthesis /
    validation backend sees a flat load regardless of replica count, with
    cross-node priority and crash-safe leases.

Vertical scaling is ``UASR_REPAIR_MAX_CONCURRENT`` (local) /
``UASR_REPAIR_MAX_GLOBAL_CONCURRENT`` (distributed): how many recoveries a box —
or the fleet — runs at once, sized to the host's cores / backend budget.

Redis is imported lazily and only when a redis-backed mode is selected, so the
default (memory + local) deployment has no redis dependency at all.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from .state_store import InMemoryStateStore, RedisStateStore, StateStore


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _truthy(name: str, default: str = "false") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


# ────────────────────────────────────────────────────────────────────
# Redis client (lazy, shared)
# ────────────────────────────────────────────────────────────────────

def build_redis_client(url: Optional[str] = None) -> Any:
    """Construct a redis client from ``UASR_REDIS_URL`` (or ``url``).

    Imported lazily so the default memory/local deployment never needs the
    ``redis`` package installed.  Raises a clear error if a redis-backed mode is
    selected but the dependency is missing.
    """
    url = url or _env("UASR_REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency-guard path
        raise RuntimeError(
            "A redis-backed UASR mode was selected (UASR_STATE_BACKEND=redis or "
            "UASR_REPAIR_BACKEND=distributed) but the 'redis' package is not "
            "installed. Install redis>=5.0 or use the memory/local backends."
        ) from exc
    return redis.Redis.from_url(url)


# ────────────────────────────────────────────────────────────────────
# State backend
# ────────────────────────────────────────────────────────────────────

def build_state_store(redis_client: Any = None) -> StateStore:
    """Build the detector's :class:`StateStore` from environment config."""
    backend = _env("UASR_STATE_BACKEND", "memory").lower()
    if backend in ("memory", "inmemory", "in_memory", ""):
        capacity = _env_int("UASR_STATE_CAPACITY", None)
        return InMemoryStateStore(capacity=capacity)
    if backend == "redis":
        client = redis_client or build_redis_client()
        prefix = _env("UASR_REDIS_STATE_PREFIX", "uasr:state:")
        ttl = _env_int("UASR_STATE_TTL_SECONDS", None)
        url = _env("UASR_REDIS_URL", "redis://localhost:6379/0")
        return RedisStateStore(url=url, prefix=prefix, ttl_seconds=ttl, client=client)
    raise ValueError(f"Unknown UASR_STATE_BACKEND={backend!r} (expected memory|redis)")


# ────────────────────────────────────────────────────────────────────
# Repair admission backend
# ────────────────────────────────────────────────────────────────────

def build_repair_scheduler(redis_client: Any = None) -> Optional[Any]:
    """Build the repair-admission backend from environment config.

    Returns ``None`` when repair scheduling is disabled (``UASR_REPAIR_BACKEND=
    none``) — the worker then awaits recoveries directly, matching legacy
    one-worker-per-process behaviour.

    The returned object exposes ``submit(source_id, severity, coro_factory)`` in
    both modes, so :class:`MAPEKWorker` routes through either interchangeably.
    ``local`` schedulers additionally expose ``start()``/``stop()`` for the
    caller's lifespan to manage; the distributed coordinator needs no background
    task (admission polls inside ``submit``).
    """
    backend = _env("UASR_REPAIR_BACKEND", "local").lower()
    if backend in ("none", "off", "disabled"):
        return None
    if backend == "local":
        from .repair_scheduler import RepairScheduler
        cap = _env_int("UASR_REPAIR_MAX_CONCURRENT", 4) or 4
        return RepairScheduler(max_concurrent=cap)
    if backend in ("distributed", "redis", "fleet"):
        from .distributed_repair import DistributedRepairCoordinator
        client = redis_client or build_redis_client()
        cap = _env_int("UASR_REPAIR_MAX_GLOBAL_CONCURRENT", 8) or 8
        namespace = _env("UASR_REPAIR_NAMESPACE", "uasr:repair")
        lease_ms = _env_int("UASR_REPAIR_LEASE_MS", 30_000) or 30_000
        return DistributedRepairCoordinator(
            client=client,
            max_global_concurrent=cap,
            namespace=namespace,
            lease_ms=lease_ms,
        )
    raise ValueError(
        f"Unknown UASR_REPAIR_BACKEND={backend!r} (expected local|distributed|none)"
    )


# ────────────────────────────────────────────────────────────────────
# Deployment-mode summary (for /health, logs, and the readiness probe)
# ────────────────────────────────────────────────────────────────────

def deployment_summary() -> dict:
    """A JSON-able snapshot of the active deployment mode, for observability."""
    state_backend = _env("UASR_STATE_BACKEND", "memory").lower()
    repair_backend = _env("UASR_REPAIR_BACKEND", "local").lower()
    summary = {
        "state_backend": state_backend,
        "repair_backend": repair_backend,
        "mapek_enabled": _truthy("UASR_MAPEK_ENABLED"),
        "recovery_mode": _env("UASR_RECOVERY_MODE", "auto"),
        "risk_tiered": _truthy("UASR_RISK_TIERED"),
    }
    if state_backend == "redis" or repair_backend in ("distributed", "redis", "fleet"):
        summary["redis_url"] = _env("UASR_REDIS_URL", "redis://localhost:6379/0")
    if state_backend == "memory":
        summary["state_capacity"] = _env_int("UASR_STATE_CAPACITY", None)
    if repair_backend == "local":
        summary["repair_max_concurrent"] = _env_int("UASR_REPAIR_MAX_CONCURRENT", 4)
    elif repair_backend in ("distributed", "redis", "fleet"):
        summary["repair_max_global_concurrent"] = _env_int("UASR_REPAIR_MAX_GLOBAL_CONCURRENT", 8)
    # horizontal fan-out is set by the orchestrator (replica count); we surface
    # this node's identity so a fleet's /health responses are distinguishable.
    summary["node_id"] = _env("UASR_NODE_ID", os.getenv("HOSTNAME", "local"))
    return summary
