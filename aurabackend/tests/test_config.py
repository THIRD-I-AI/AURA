"""
AURA Config Tests
==================
Tests for AuraSettings validation and derived properties.
"""
from __future__ import annotations

import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAuraSettings:
    def test_default_settings_load(self):
        """Default settings should load without error in development."""
        from shared.config import AuraSettings
        s = AuraSettings(
            _env_file=None,
            ENVIRONMENT="development",
            SECRET_KEY="test-secret",
        )
        assert s.environment == "development"
        assert s.api_gateway_port == 8000

    def test_is_production_property(self):
        from shared.config import AuraSettings
        dev = AuraSettings(_env_file=None, ENVIRONMENT="development", SECRET_KEY="x")
        prod = AuraSettings(_env_file=None, ENVIRONMENT="production", SECRET_KEY="real-secret")
        assert dev.is_production is False
        assert prod.is_production is True

    def test_production_rejects_default_secret_key(self):
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="SECRET_KEY must be set in production"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="change-me-in-production",
            )

    def test_development_warns_default_secret_key(self):
        from shared.config import AuraSettings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="development",
                SECRET_KEY="change-me-in-production",
            )
            secret_warnings = [x for x in w if "SECRET_KEY" in str(x.message)]
            assert len(secret_warnings) >= 1

    def test_cors_string_parsing(self):
        from shared.config import AuraSettings
        s = AuraSettings(
            _env_file=None,
            CORS_ALLOWED_ORIGINS="http://a.com, http://b.com",
            SECRET_KEY="x",
        )
        assert s.cors_origins == ["http://a.com", "http://b.com"]

    def test_cors_wildcard_rejected_in_production(self):
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="CORS wildcard"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="real-secret",
                CORS_ALLOWED_ORIGINS="*",
            )

    def test_auth_mode_default(self):
        from shared.config import AuraSettings
        s = AuraSettings(_env_file=None, SECRET_KEY="x")
        assert s.auth_mode == "open"

    def test_redis_url_default_none(self):
        from shared.config import AuraSettings
        s = AuraSettings(_env_file=None, SECRET_KEY="x")
        assert s.redis_url is None

    def test_db_dsn_property(self):
        from shared.config import AuraSettings
        s = AuraSettings(
            _env_file=None,
            SECRET_KEY="x",
            DB_HOST="myhost",
            DB_PORT="5433",
            DB_NAME="mydb",
            DB_USER="myuser",
            DB_PASSWORD="mypass",
        )
        assert "myhost:5433/mydb" in s.db_dsn
        assert "myuser:mypass" in s.db_dsn
