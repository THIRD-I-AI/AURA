"""
AURA observability bootstrap
=============================
Single entry point for Prometheus + Sentry. Both are no-ops when their
respective environment switches are unset, so importing this module is
free for tests and local dev.

Env switches:
  AURA_METRICS_ENABLED   — '1'/'true' to expose /metrics (default: enabled)
  AURA_SENTRY_DSN        — DSN string; absence disables Sentry entirely
  AURA_SENTRY_ENV        — environment tag (default: settings.environment)
  AURA_SENTRY_TRACES_RATE— float 0..1 (default: 0.0 — errors only)

Custom counters live at the bottom of this module so call sites stay terse:
    from shared.observability import CHAT_REQUESTS, AGENT_DURATION, LLM_TOKENS
    CHAT_REQUESTS.labels(status="ok").inc()
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI

from shared.config import settings
from shared.logging_config import get_logger

logger = get_logger("aura.observability")

# ─────────────────────────────────────────────────────────────────────
# Prometheus
# ─────────────────────────────────────────────────────────────────────

try:
    from prometheus_client import Counter, Histogram
    _PROM_AVAILABLE = True
except ImportError:  # pragma: no cover — optional dep
    _PROM_AVAILABLE = False
    Counter = None  # type: ignore[assignment]
    Histogram = None  # type: ignore[assignment]

try:
    from prometheus_fastapi_instrumentator import Instrumentator
    _INSTRUMENTATOR_AVAILABLE = True
except ImportError:  # pragma: no cover — optional dep
    _INSTRUMENTATOR_AVAILABLE = False
    Instrumentator = None  # type: ignore[assignment]


def _metrics_enabled() -> bool:
    raw = os.getenv("AURA_METRICS_ENABLED", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def init_metrics(app: FastAPI, *, service_tag: str) -> None:
    """Mount /metrics on the given app. No-op if the dep is missing or disabled."""
    if not _INSTRUMENTATOR_AVAILABLE:
        logger.debug("prometheus-fastapi-instrumentator not installed; skipping /metrics on %s", service_tag)
        return
    if not _metrics_enabled():
        logger.info("metrics disabled by AURA_METRICS_ENABLED for %s", service_tag)
        return

    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health"],
    )
    instrumentator.instrument(app).expose(app, include_in_schema=False, tags=["observability"])
    logger.info("Prometheus /metrics exposed for %s", service_tag)


# ─────────────────────────────────────────────────────────────────────
# Sentry
# ─────────────────────────────────────────────────────────────────────

try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration

    _SENTRY_AVAILABLE = True
except ImportError:  # pragma: no cover — optional dep
    _SENTRY_AVAILABLE = False
    sentry_sdk = None  # type: ignore[assignment]
    FastApiIntegration = None  # type: ignore[assignment]


_sentry_initialized = False


def init_sentry(*, service_tag: str) -> None:
    """Initialize the global Sentry client once. No-op when DSN unset."""
    global _sentry_initialized
    if _sentry_initialized:
        return
    dsn = os.getenv("AURA_SENTRY_DSN") or os.getenv("SENTRY_DSN")
    if not dsn:
        return
    if not _SENTRY_AVAILABLE:
        logger.warning("AURA_SENTRY_DSN set but sentry-sdk not installed")
        return
    try:
        traces_rate = float(os.getenv("AURA_SENTRY_TRACES_RATE", "0.0"))
    except ValueError:
        traces_rate = 0.0
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("AURA_SENTRY_ENV", settings.environment),
        traces_sample_rate=traces_rate,
        integrations=[FastApiIntegration()],
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", service_tag)
    _sentry_initialized = True
    logger.info("Sentry initialized for %s (env=%s)", service_tag, settings.environment)


# ─────────────────────────────────────────────────────────────────────
# Custom AURA metrics
# ─────────────────────────────────────────────────────────────────────
# Always defined so call sites don't need to guard imports. When the
# prometheus client is missing we fall back to no-op stubs that swallow
# the labels()/inc()/observe() chain.

class _NoopMetric:
    def labels(self, *_: object, **__: object) -> "_NoopMetric":
        return self

    def inc(self, _amount: float = 1) -> None:
        pass

    def observe(self, _value: float) -> None:
        pass


if _PROM_AVAILABLE:
    CHAT_REQUESTS = Counter(
        "aura_chat_requests_total",
        "Total /chat requests served",
        labelnames=("status",),
    )
    LLM_TOKENS = Counter(
        "aura_llm_tokens_total",
        "Total LLM tokens consumed",
        labelnames=("provider", "model", "kind"),  # kind in {prompt, completion}
    )
    AGENT_DURATION = Histogram(
        "aura_agent_duration_seconds",
        "Per-agent execution time",
        labelnames=("agent",),
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    )
else:  # pragma: no cover — exercised only when dep is missing
    CHAT_REQUESTS = _NoopMetric()  # type: ignore[assignment]
    LLM_TOKENS = _NoopMetric()  # type: ignore[assignment]
    AGENT_DURATION = _NoopMetric()  # type: ignore[assignment]


def llm_token_breakdown() -> Dict[str, Any]:
    """Snapshot the LLM_TOKENS counter as a structured breakdown.

    Returns ``{"available": bool, "rows": [...], "totals": {...}}`` where each
    row has ``provider``, ``model``, ``kind`` and ``tokens``. ``kind`` is one of
    ``prompt``, ``completion``, ``cached_completion`` (cached tokens are also
    counted in ``completion`` — they're a subset, not a separate bucket).

    Returns ``available=False`` when prometheus-client isn't installed.
    """
    if not _PROM_AVAILABLE:
        return {"available": False, "rows": [], "totals": {}}
    rows: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {"prompt": 0.0, "completion": 0.0, "cached_completion": 0.0}
    try:
        for metric in LLM_TOKENS.collect():  # type: ignore[union-attr]
            for sample in metric.samples:
                if not sample.name.endswith("_total"):
                    continue
                labels = sample.labels
                kind = labels.get("kind", "")
                row = {
                    "provider": labels.get("provider", "unknown"),
                    "model": labels.get("model", ""),
                    "kind": kind,
                    "tokens": float(sample.value),
                }
                rows.append(row)
                if kind in totals:
                    totals[kind] += row["tokens"]
    except Exception:
        return {"available": False, "rows": [], "totals": totals}
    return {"available": True, "rows": rows, "totals": totals}


__all__ = [
    "init_metrics",
    "init_sentry",
    "CHAT_REQUESTS",
    "LLM_TOKENS",
    "AGENT_DURATION",
    "llm_token_breakdown",
]
