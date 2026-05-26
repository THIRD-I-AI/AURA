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

    # Sec-4 added two more production-mode validators (auth_mode and
    # CORS http-rejection). Every test that instantiates a production
    # Settings must now supply valid AURA_AUTH_MODE + HTTPS-only CORS
    # origins, otherwise it would cascade-fail on those defaults before
    # exercising the assertion target.
    _PROD_VALID = {
        "AURA_AUTH_MODE": "password",
        "CORS_ALLOWED_ORIGINS": "https://app.example.com",
    }

    def test_is_production_property(self):
        from shared.config import AuraSettings
        dev = AuraSettings(_env_file=None, ENVIRONMENT="development", SECRET_KEY="x")
        prod = AuraSettings(
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="real-secret",
            **self._PROD_VALID,
        )
        assert dev.is_production is False
        assert prod.is_production is True

    def test_production_rejects_default_secret_key(self):
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="SECRET_KEY must be set in production"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="change-me-in-production",
                **self._PROD_VALID,
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
                AURA_AUTH_MODE="password",
                CORS_ALLOWED_ORIGINS="*",
            )

    def test_cors_http_origin_rejected_in_production(self):
        # Sec-4: HTTP origin in production CORS list is a hard rejection
        # now (was a warning pre-Sec-4).
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="Non-HTTPS CORS origin"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="real-secret",
                AURA_AUTH_MODE="password",
                CORS_ALLOWED_ORIGINS="http://api.example.com",
            )

    def test_cors_https_origin_allowed_in_production(self):
        # Sanity: HTTPS origins remain allowed.
        from shared.config import AuraSettings
        s = AuraSettings(
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="real-secret",
            AURA_AUTH_MODE="password",
            CORS_ALLOWED_ORIGINS="https://api.example.com",
        )
        assert s.cors_origins == ["https://api.example.com"]

    def test_cors_http_allowed_in_development(self):
        # Localhost / dev flows MUST keep working with http:// origins.
        from shared.config import AuraSettings
        s = AuraSettings(
            _env_file=None,
            ENVIRONMENT="development",
            SECRET_KEY="x",
            CORS_ALLOWED_ORIGINS="http://localhost:5173",
        )
        assert s.cors_origins == ["http://localhost:5173"]

    def test_production_rejects_open_auth_mode(self):
        # Sec-4: auth_mode='open' is a credential-validation bypass; a
        # default-config production deploy must fail at startup rather
        # than silently mint unauthenticated tokens.
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="auth_mode='open' is not allowed"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="real-secret",
                AURA_AUTH_MODE="open",
                CORS_ALLOWED_ORIGINS="https://app.example.com",
            )

    def test_production_allows_password_auth_mode(self):
        from shared.config import AuraSettings
        s = AuraSettings(
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="real-secret",
            AURA_AUTH_MODE="password",
            CORS_ALLOWED_ORIGINS="https://app.example.com",
        )
        assert s.auth_mode == "password"

    def test_trust_forwarded_for_defaults_false(self):
        # Sec-4: X-Forwarded-For is spoofable; only honour when
        # explicitly opted in via env var.
        from shared.config import AuraSettings
        s = AuraSettings(_env_file=None, SECRET_KEY="x")
        assert s.trust_forwarded_for is False

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
