"""
AURA Secret Resolver Tests
============================
Tests for SecretResolver with env-var fallback and mocked Key Vault client.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.secret_resolver import SecretResolver

# ── Env-var fallback (no vault configured) ───────────────────────────

def test_get_secret_from_env():
    with patch.dict(os.environ, {"MY_SECRET": "secret_value", "KEY_VAULT_URI": ""}, clear=False):
        resolver = SecretResolver()
        assert resolver._client is None
        result = resolver.get_secret("MY_SECRET")
        assert result == "secret_value"


def test_get_secret_missing_returns_none():
    with patch.dict(os.environ, {"KEY_VAULT_URI": ""}, clear=False):
        resolver = SecretResolver()
        result = resolver.get_secret("TOTALLY_MISSING_SECRET_XYZ_12345")
        assert result is None or result == os.getenv("TOTALLY_MISSING_SECRET_XYZ_12345")


# ── Vault client mock ───────────────────────────────────────────────

def test_get_secret_from_vault():
    resolver = SecretResolver.__new__(SecretResolver)
    mock_client = MagicMock()
    mock_secret = MagicMock()
    mock_secret.value = "vault_value"
    mock_client.get_secret.return_value = mock_secret
    resolver._client = mock_client
    resolver._vault_uri = "https://myvault.vault.azure.net"
    resolver._cache = {}

    result = resolver.get_secret("DB_PASSWORD")
    assert result == "vault_value"
    mock_client.get_secret.assert_called_once_with("DB_PASSWORD")


def test_vault_exception_falls_back_to_env():
    with patch.dict(os.environ, {"DB_PASSWORD": "env_fallback"}, clear=False):
        resolver = SecretResolver.__new__(SecretResolver)
        mock_client = MagicMock()
        mock_client.get_secret.side_effect = Exception("vault unreachable")
        resolver._client = mock_client
        resolver._vault_uri = "https://myvault.vault.azure.net"
        resolver._cache = {}

        result = resolver.get_secret("DB_PASSWORD")
        assert result == "env_fallback"


def test_cache_avoids_repeated_lookups():
    """Per-instance cache should memoize vault lookups."""
    resolver = SecretResolver.__new__(SecretResolver)
    mock_client = MagicMock()
    mock_secret = MagicMock()
    mock_secret.value = "cached_value"
    mock_client.get_secret.return_value = mock_secret
    resolver._client = mock_client
    resolver._vault_uri = "https://v.vault.azure.net"
    resolver._cache = {}

    resolver.get_secret("KEY")
    resolver.get_secret("KEY")
    # Second call hits the cache, not the client
    assert mock_client.get_secret.call_count == 1


# ── _build_client returns None when no vault URI ────────────────────

def test_build_client_no_uri():
    with patch.dict(os.environ, {"KEY_VAULT_URI": ""}, clear=False):
        resolver = SecretResolver()
        assert resolver._client is None


def test_build_client_no_azure_packages():
    with patch.dict(os.environ, {"KEY_VAULT_URI": "https://vault.azure.net"}, clear=False):
        with patch("shared.secret_resolver.DefaultAzureCredential", None):
            resolver = SecretResolver()
            assert resolver._client is None
