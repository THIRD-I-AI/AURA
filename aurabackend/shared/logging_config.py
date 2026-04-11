"""
AURA Centralized Logging
=========================
Configures structured, consistent logging across all microservices.

Usage:
    from shared.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Service started", extra={"port": 8000})
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

_CONFIGURED = False


def setup_logging(
    level: str = "INFO",
    fmt: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    log_file: Optional[str] = None,
) -> None:
    """
    Configure the root logger once.  Safe to call multiple times (idempotent).

    Parameters
    ----------
    level : str
        Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    fmt : str
        Format string for log lines.
    log_file : str | None
        Optional file path.  If provided, a rotating file handler is added.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler (stdout) ────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (optional, rotating 10 MB × 5 backups) ────────────────
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Quiet noisy third-party loggers ─────────────────────────────────────
    for noisy in ("httpcore", "httpx", "urllib3", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger, ensuring setup_logging() has been called.

    Lazily reads from ``shared.config.settings`` if available so that import
    order doesn't matter.
    """
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
