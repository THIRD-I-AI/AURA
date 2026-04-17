"""
AURA Kafka Client Tests
=========================
Tests for the _parse_message helper and consume_batch config validation.
The actual Kafka consumer is not started — only the pure logic is tested.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.kafka_client import _parse_message

# ── _parse_message ────────────────────────────────────────────────

class TestParseMessage:
    def test_none_value(self):
        result = _parse_message(None, "json")
        assert result == {"_raw": None}

    def test_valid_json_dict(self):
        raw = b'{"key": "value", "num": 42}'
        result = _parse_message(raw, "json")
        assert result == {"key": "value", "num": 42}

    def test_valid_json_non_dict(self):
        raw = b'[1, 2, 3]'
        result = _parse_message(raw, "json")
        assert result == {"_value": [1, 2, 3]}

    def test_invalid_json(self):
        raw = b'not valid json'
        result = _parse_message(raw, "json")
        assert result == {"_raw": "not valid json"}

    def test_ndjson_format(self):
        raw = b'{"a": 1}'
        result = _parse_message(raw, "ndjson-string")
        assert result == {"a": 1}

    def test_unknown_format(self):
        raw = b'plain text'
        result = _parse_message(raw, "text")
        assert result == {"_raw": "plain text"}

    def test_binary_decode_error(self):
        raw = b'\x80\x81\x82'
        result = _parse_message(raw, "json")
        assert "_raw" in result

    def test_empty_json_object(self):
        raw = b'{}'
        result = _parse_message(raw, "json")
        assert result == {}

    def test_nested_json(self):
        raw = b'{"outer": {"inner": "value"}}'
        result = _parse_message(raw, "json")
        assert result["outer"]["inner"] == "value"


# ── consume_batch validation ─────────────────────────────────────

class TestConsumeBatchValidation:
    @pytest.mark.asyncio
    async def test_missing_aiokafka_raises_runtime_error(self):
        """If aiokafka is not importable, RuntimeError should be raised."""
        import importlib
        from unittest.mock import patch

        # Hide aiokafka so the lazy import inside consume_batch fails
        with patch.dict("sys.modules", {"aiokafka": None, "aiokafka.errors": None}):
            import shared.kafka_client as kmod
            importlib.reload(kmod)
            with pytest.raises(RuntimeError, match="aiokafka is not installed"):
                await kmod.consume_batch({"bootstrap_servers": "x", "topic": "t"})
            # Restore module
            importlib.reload(kmod)

    @pytest.mark.asyncio
    async def test_missing_bootstrap_with_mock(self):
        """With aiokafka mocked away, missing config should raise ValueError."""
        from unittest.mock import MagicMock, patch
        mock_aiokafka = MagicMock()
        mock_errors = MagicMock()
        with patch.dict("sys.modules", {
            "aiokafka": mock_aiokafka,
            "aiokafka.errors": mock_errors,
        }):
            # Re-import to pick up the mock
            import importlib

            import shared.kafka_client as kmod
            importlib.reload(kmod)
            with pytest.raises(ValueError, match="bootstrap_servers"):
                await kmod.consume_batch({"topic": "test"})

    @pytest.mark.asyncio
    async def test_missing_topic_with_mock(self):
        """With aiokafka mocked away, missing topic should raise ValueError."""
        from unittest.mock import MagicMock, patch
        mock_aiokafka = MagicMock()
        mock_errors = MagicMock()
        with patch.dict("sys.modules", {
            "aiokafka": mock_aiokafka,
            "aiokafka.errors": mock_errors,
        }):
            import importlib

            import shared.kafka_client as kmod
            importlib.reload(kmod)
            with pytest.raises(ValueError, match="topic"):
                await kmod.consume_batch({"bootstrap_servers": "localhost:9092"})
