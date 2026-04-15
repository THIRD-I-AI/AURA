"""
AURA Centralized Logging
=========================
Configures consistent logging across all microservices.

Features
--------
- Idempotent `setup_logging()` — safe to call from every process/service.
- Optional structured JSON output via `AURA_LOG_JSON=1` (one log line =
  one JSON object, ready for Loki/Datadog/CloudWatch).
- Correlation ID injection: any LogRecord emitted while a request is in
  flight (see `shared.middleware.RequestIDMiddleware`) automatically gets
  a `request_id` field via `bind_request_id()`.
- Rotating file handler when `LOG_FILE` is set.

Usage
-----
    from shared.logging_config import get_logger, bind_request_id
    logger = get_logger(__name__)
    logger.info("processed", extra={"rows": 42})
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Optional

_CONFIGURED = False

# Context var is set by RequestIDMiddleware per-request so every log line
# emitted while handling that request carries the correlation id — even when
# deep callers don't know about it.
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "aura_request_id", default=None,
)


def bind_request_id(request_id: Optional[str]) -> contextvars.Token[Optional[str]]:
    """Bind a correlation id to the current async task / thread. Returns a
    reset token; pass it to :func:`reset_request_id` to pop the binding."""
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[Optional[str]]) -> None:
    _request_id_var.reset(token)


def current_request_id() -> Optional[str]:
    return _request_id_var.get()


class _RequestIdFilter(logging.Filter):
    """Injects `record.request_id` from the current contextvar."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if not hasattr(record, "request_id") or not record.request_id:
            record.request_id = _request_id_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """Minimal JSON formatter — one line per record, no deps."""

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Forward any user-supplied extras.
        for k, v in record.__dict__.items():
            if k in self._RESERVED or k == "request_id":
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, default=str)


def setup_logging(
    level: str = "INFO",
    fmt: str = "%(asctime)s | %(levelname)-8s | %(name)s | [%(request_id)s] %(message)s",
    log_file: Optional[str] = None,
    json_output: Optional[bool] = None,
) -> None:
    """
    Configure the root logger once.  Safe to call multiple times (idempotent).

    Parameters
    ----------
    level : str
        Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    fmt : str
        Format string for plain-text mode. Ignored when JSON mode is on.
    log_file : str | None
        Optional file path. If provided, a rotating file handler is added.
    json_output : bool | None
        Force JSON output on/off. When None, reads `AURA_LOG_JSON` env var.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if json_output is None:
        json_output = os.getenv("AURA_LOG_JSON", "").strip().lower() in ("1", "true", "yes")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter: logging.Formatter
    if json_output:
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    req_filter = _RequestIdFilter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(req_filter)
    root.addHandler(console)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(req_filter)
        root.addHandler(file_handler)

    for noisy in ("httpcore", "httpx", "urllib3", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring setup_logging() has been called."""
    if not _CONFIGURED:
        try:
            from shared.config import settings
            setup_logging(
                level=settings.log_level,
                fmt=settings.log_format,
                log_file=settings.log_file,
            )
        except Exception:
            setup_logging()

    return logging.getLogger(name)
