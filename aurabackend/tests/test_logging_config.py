"""
AURA Logging Config Tests
===========================
Tests for setup_logging, JSON formatter, request-ID binding, and get_logger.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.logging_config as lc
from shared.logging_config import (
    _JsonFormatter,
    _RequestIdFilter,
    bind_request_id,
    current_request_id,
    reset_request_id,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Reset the module-level _CONFIGURED flag before each test."""
    lc._CONFIGURED = False
    # Clean up any handlers we add during tests
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    yield
    lc._CONFIGURED = False
    root.handlers = original_handlers


# ── Request ID context var ───────────────────────────────────────────

def test_bind_and_read_request_id():
    token = bind_request_id("req-123")
    assert current_request_id() == "req-123"
    reset_request_id(token)
    assert current_request_id() is None


def test_default_request_id_is_none():
    assert current_request_id() is None


# ── _RequestIdFilter ────────────────────────────────────────────────

def test_request_id_filter_injects_id():
    token = bind_request_id("test-filter-id")
    try:
        f = _RequestIdFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        result = f.filter(record)
        assert result is True
        assert record.request_id == "test-filter-id"
    finally:
        reset_request_id(token)


def test_request_id_filter_default_dash():
    f = _RequestIdFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    f.filter(record)
    assert record.request_id == "-"


def test_request_id_filter_preserves_existing():
    f = _RequestIdFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    record.request_id = "already-set"
    f.filter(record)
    assert record.request_id == "already-set"


# ── _JsonFormatter ──────────────────────────────────────────────────

def test_json_formatter_basic():
    fmt = _JsonFormatter()
    record = logging.LogRecord("mylogger", logging.WARNING, "", 0, "hello", (), None)
    record.request_id = "r-1"
    output = fmt.format(record)
    data = json.loads(output)
    assert data["level"] == "WARNING"
    assert data["logger"] == "mylogger"
    assert data["msg"] == "hello"
    assert data["request_id"] == "r-1"


def test_json_formatter_with_exception():
    fmt = _JsonFormatter()
    try:
        raise ValueError("test error")
    except ValueError:
        record = logging.LogRecord(
            "mylogger", logging.ERROR, "", 0, "fail", (), sys.exc_info()
        )
    record.request_id = "-"
    output = fmt.format(record)
    data = json.loads(output)
    assert "exc" in data
    assert "ValueError" in data["exc"]


def test_json_formatter_extra_fields():
    fmt = _JsonFormatter()
    record = logging.LogRecord("mylogger", logging.INFO, "", 0, "msg", (), None)
    record.request_id = "-"
    record.rows = 42  # user-supplied extra
    output = fmt.format(record)
    data = json.loads(output)
    assert data["rows"] == 42


def test_json_formatter_non_serializable_extra():
    fmt = _JsonFormatter()
    record = logging.LogRecord("mylogger", logging.INFO, "", 0, "msg", (), None)
    record.request_id = "-"
    record.weird = object()  # not JSON serializable
    output = fmt.format(record)
    data = json.loads(output)
    assert "weird" in data


# ── setup_logging ───────────────────────────────────────────────────

def test_setup_logging_idempotent():
    setup_logging(level="DEBUG")
    handler_count = len(logging.getLogger().handlers)
    setup_logging(level="DEBUG")  # second call should be no-op
    assert len(logging.getLogger().handlers) == handler_count


def test_setup_logging_json_mode():
    setup_logging(level="INFO", json_output=True)
    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, _JsonFormatter)]
    assert len(json_handlers) >= 1


def test_setup_logging_env_var_json():
    with patch.dict(os.environ, {"AURA_LOG_JSON": "1"}):
        setup_logging()
    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, _JsonFormatter)]
    assert len(json_handlers) >= 1


def test_setup_logging_with_file():
    tmpdir = tempfile.mkdtemp()
    try:
        log_file = os.path.join(tmpdir, "test.log")
        setup_logging(level="DEBUG", log_file=log_file)
        logger = logging.getLogger("test_file_handler")
        logger.info("test message")
        assert os.path.exists(log_file)
    finally:
        # Close file handlers to release the log file on Windows
        root = logging.getLogger()
        for h in root.handlers[:]:
            if isinstance(h, logging.handlers.RotatingFileHandler):
                h.close()
                root.removeHandler(h)


def test_setup_logging_plain_text():
    setup_logging(level="WARNING", json_output=False)
    root = logging.getLogger()
    assert root.level == logging.WARNING


# ── get_logger ──────────────────────────────────────────────────────

def test_get_logger_returns_named_logger():
    lc._CONFIGURED = True  # skip auto-setup
    lgr = lc.get_logger("my.module")
    assert lgr.name == "my.module"


def test_get_logger_triggers_setup():
    lc._CONFIGURED = False
    with patch("shared.logging_config.setup_logging") as mock_setup:
        mock_setup.side_effect = lambda **kw: setattr(lc, "_CONFIGURED", True)
        with patch.dict("sys.modules", {"shared.config": None}):
            lgr = lc.get_logger("fallback.test")
            assert lgr.name == "fallback.test"
