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
    # CORS http-rejection); the SQL-hardening pass added a third
    # (AURA_JWT_ENABLED, required for tenant isolation). Every test that
    # instantiates a *valid* production Settings must supply all three,
    # otherwise it would cascade-fail on those defaults before exercising
    # the assertion target.
    # The minimum a production config must satisfy. Each entry corresponds to
    # a fail-closed validator in shared/config.py, so adding a new production
    # requirement surfaces here rather than at every construction site.
    _PROD_VALID = {
        "AURA_AUTH_MODE": "password",
        "AURA_JWT_ENABLED": "true",
        "AURA_AUDIT_ENABLED": "true",
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
            AURA_JWT_ENABLED="true",
            AURA_AUDIT_ENABLED="true",
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
            **self._PROD_VALID,
        )
        assert s.auth_mode == "password"

    def test_production_rejects_jwt_disabled(self):
        # SQL-hardening pass: tenant isolation is enforced only with the JWT
        # middleware active. A production deploy with AURA_JWT_ENABLED=false
        # silently shares all data under the 'default' tenant — fail at startup.
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="AURA_JWT_ENABLED must be true"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="real-secret",
                AURA_AUTH_MODE="password",
                AURA_JWT_ENABLED="false",
                CORS_ALLOWED_ORIGINS="https://app.example.com",
            )

    def test_development_allows_jwt_disabled(self):
        # The default (jwt disabled) stays valid outside production so local
        # single-user dev keeps working without auth wiring.
        from shared.config import AuraSettings
        s = AuraSettings(_env_file=None, ENVIRONMENT="development", SECRET_KEY="x")
        assert s.jwt_enabled is False

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


class TestProductionEnvFailsClosed:
    """Every production safeguard hangs off `_is_production_env`: JWT
    enforcement (and therefore tenant isolation), the open-auth-mode rejection,
    the SECRET_KEY check, and the ephemeral-signing-key refusal. It used to
    match an allowlist of exactly {"production", "prod"}, so a deploy named
    `prod-eu` or `staging` silently disabled ALL of them at once — every route
    unauthenticated, every request collapsed into one shared 'default'
    workspace. These pin the inverted, fail-closed behaviour."""

    @pytest.mark.parametrize("name", [
        "development", "dev", "devel", "test", "testing", "ci",
        "local", "localhost", "docker", "sandbox", "demo",
        "DEVELOPMENT", "  dev  ",  # case + surrounding whitespace normalised
    ])
    def test_known_non_production_names(self, name):
        from shared.config import _is_production_env
        assert _is_production_env(name) is False

    @pytest.mark.parametrize("name", [
        "production", "prod", "PROD",
        # The regression this class exists for: every one of these previously
        # returned False and disabled all production guards simultaneously.
        "prod-eu", "prod-us-east-1", "production-eu", "staging", "live",
        "preprod", "uat", "prd", "typo-here", "",
    ])
    def test_unrecognised_names_are_production(self, name):
        from shared.config import _is_production_env
        assert _is_production_env(name) is True

    def test_jwt_disabled_in_prod_like_env_now_fails_loudly(self):
        """The payoff: a realistic multi-region name refuses to boot rather
        than silently serving every tenant from one shared workspace."""
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="AURA_JWT_ENABLED must be true"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="prod-eu",
                AURA_JWT_ENABLED=False,
                AURA_AUTH_MODE="password",
                SECRET_KEY="x" * 48,
            )

    def test_dev_still_boots_with_jwt_disabled(self):
        """The inversion must not break ordinary local development."""
        from shared.config import AuraSettings
        s = AuraSettings(_env_file=None, ENVIRONMENT="development",
                         AURA_JWT_ENABLED=False, SECRET_KEY="x")
        assert s.is_production is False


class TestAuditTrailFailsClosedInProduction:
    """AURA_AUDIT_ENABLED was read by a bare os.getenv in shared/audit_log.py,
    defaulting to false, and was not a settings field at all — so none of the
    production validators could cover it. A deploy could satisfy every other
    hardening gate (JWT required, no open auth mode, no wildcard CORS) while
    the durable hash-chained audit trail was silently off. For a product sold
    to banks on provable audit trails, that is the worst thing to fail open."""

    def test_audit_disabled_in_production_refuses_to_boot(self):
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="AURA_AUDIT_ENABLED must be true"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="production",
                AURA_AUDIT_ENABLED=False,
                AURA_JWT_ENABLED=True,
                AURA_AUTH_MODE="password",
                SECRET_KEY="x" * 48,
            )

    def test_audit_disabled_in_development_is_fine(self):
        from shared.config import AuraSettings
        s = AuraSettings(_env_file=None, ENVIRONMENT="development",
                         AURA_AUDIT_ENABLED=False, SECRET_KEY="x")
        assert s.audit_enabled is False

    def test_prod_like_env_name_is_also_covered(self):
        """Inherits the fail-closed env-name check: a region-suffixed name
        must not slip past this validator either."""
        from shared.config import AuraSettings
        with pytest.raises(ValueError, match="AURA_AUDIT_ENABLED must be true"):
            AuraSettings(
                _env_file=None,
                ENVIRONMENT="prod-eu",
                AURA_AUDIT_ENABLED=False,
                AURA_JWT_ENABLED=True,
                AURA_AUTH_MODE="password",
                SECRET_KEY="x" * 48,
            )
