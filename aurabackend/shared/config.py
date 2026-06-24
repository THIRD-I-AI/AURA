"""
AURA Centralized Configuration
================================
Single source of truth for all environment variables and settings.
Uses Pydantic BaseSettings for validation, type coercion, and .env loading.

Usage:
    from shared.config import settings
    print(settings.api_gateway_port)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings


def _locate_env_files() -> list[str]:
    """Return paths to .env files, closest first (aurabackend/.env then root/.env)."""
    here = Path(__file__).resolve().parent.parent          # aurabackend/
    root = here.parent                                     # project root
    candidates = [here / ".env", root / ".env"]
    return [str(p) for p in candidates if p.exists()]


class AuraSettings(BaseSettings):
    """
    All AURA configuration in one place.
    Reads from environment variables → aurabackend/.env → root .env (first wins).
    """

    # ── General ──────────────────────────────────────────────────────────
    environment: str = Field("development", alias="ENVIRONMENT")
    debug: bool = Field(True, alias="DEBUG")
    log_level: str = Field("info", alias="LOG_LEVEL")
    log_format: str = Field(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        alias="LOG_FORMAT",
    )
    log_file: Optional[str] = Field(None, alias="LOG_FILE")

    # ── AI / LLM ────────────────────────────────────────────────────────
    groq_api_key: str = Field("", alias="GROQ_API_KEY")
    groq_model: str = Field("llama-3.3-70b-versatile", alias="GROQ_MODEL")
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")
    codegen_model: str = Field("", alias="CODEGEN_MODEL")
    generator_model: str = Field("", alias="GENERATOR_MODEL")
    ollama_host: str = Field("http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field("llama3", alias="OLLAMA_MODEL")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")

    # ── Service Ports ───────────────────────────────────────────────────
    api_gateway_port: int = Field(8000, alias="API_GATEWAY_PORT")
    code_generation_port: int = Field(8001, alias="CODE_GENERATION_SERVICE_PORT")
    connectors_port: int = Field(8002, alias="CONNECTORS_PORT")
    execution_sandbox_port: int = Field(8003, alias="EXECUTION_SANDBOX_PORT")
    scheduler_port: int = Field(8004, alias="SCHEDULER_PORT")
    insights_port: int = Field(8005, alias="INSIGHTS_PORT")
    orchestration_port: int = Field(8006, alias="ORCHESTRATION_SERVICE_PORT")
    metadata_store_port: int = Field(8007, alias="METADATA_STORE_PORT")
    api_host: str = Field("0.0.0.0", alias="API_HOST")

    # ── Inter-Service URLs ──────────────────────────────────────────────
    database_service_url: str = Field("http://localhost:8002", alias="DATABASE_SERVICE_URL")
    orchestration_service_url: str = Field(
        "http://localhost:8006/v1/orchestrations/query",
        alias="ORCHESTRATION_SERVICE_URL",
    )
    codegen_url: str = Field("http://localhost:8001", alias="AURA_CODEGEN_URL")
    connectors_url: str = Field("http://localhost:8002", alias="AURA_CONNECTORS_URL")
    sandbox_url: str = Field("http://localhost:8003", alias="AURA_SANDBOX_URL")
    scheduler_url: str = Field("http://localhost:8004", alias="AURA_SCHEDULER_URL")
    insights_url: str = Field("http://localhost:8005", alias="AURA_INSIGHTS_URL")
    uasr_port: int = Field(8009, alias="UASR_SERVICE_PORT")
    uasr_url: str = Field("http://localhost:8009", alias="AURA_UASR_URL")
    api_gateway_url: str = Field("http://localhost:8000", alias="AURA_GATEWAY_URL")

    # ── CORS ────────────────────────────────────────────────────────────
    cors_origins: List[str] = Field(
        default=["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:3000"],
        alias="CORS_ALLOWED_ORIGINS",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _validate_cors_production(cls, v, info):
        env = info.data.get("environment", "development")
        if env.lower() == "production":
            if "*" in v:
                raise ValueError(
                    "CORS wildcard '*' is not allowed in production. "
                    "Set CORS_ALLOWED_ORIGINS to explicit origins."
                )
            for origin in v:
                if origin.startswith("http://"):
                    # Sec-4: HTTP origin in a production CORS list is a real
                    # MITM exposure on the cross-origin path — promote the
                    # pre-Sec-4 warning to a hard rejection, parallel to
                    # the wildcard '*' check above. An explicit env opt-in
                    # would be the right escape hatch if anyone runs prod
                    # behind a non-TLS proxy, but no caller does today.
                    raise ValueError(
                        f"Non-HTTPS CORS origin '{origin}' is not allowed "
                        "in production. Use HTTPS origins for production "
                        "deployments."
                    )
        return v

    # ── Security / Auth ─────────────────────────────────────────────────
    auth_mode: str = Field("open", alias="AURA_AUTH_MODE")
    secret_key: str = Field("change-me-in-production", alias="SECRET_KEY")
    trust_forwarded_for: bool = Field(False, alias="AURA_TRUST_FORWARDED_FOR")

    @field_validator("auth_mode", mode="after")
    @classmethod
    def _reject_open_auth_in_production(cls, v, info):
        # Sec-4: parallel to the SECRET_KEY production validator below.
        # auth_mode="open" issues JWTs without credential validation —
        # acceptable for development ergonomics but a complete
        # authentication bypass if it ever reaches a public surface.
        # An accidental ENVIRONMENT=production deployment with the
        # default config must fail loud, not silently mint tokens.
        env = info.data.get("environment", "development")
        if env.lower() == "production" and v == "open":
            raise ValueError(
                "auth_mode='open' is not allowed in production. "
                "Set AURA_AUTH_MODE=password (or another credential-"
                "validating mode) for production deployments."
            )
        return v

    @field_validator("secret_key", mode="after")
    @classmethod
    def _warn_default_secret(cls, v, info):
        if v == "change-me-in-production":
            env = info.data.get("environment", "development")
            if env.lower() == "production":
                raise ValueError(
                    "SECRET_KEY must be set in production. "
                    "The default 'change-me-in-production' value is not allowed. "
                    "Set the SECRET_KEY environment variable to a secure random string."
                )
            import warnings
            warnings.warn(
                "SECRET_KEY is still the default value. "
                "Set SECRET_KEY in your .env for production use.",
                stacklevel=2,
            )
        return v
    jwt_algorithm: str = Field("HS256", alias="ALGORITHM")
    access_token_expire_minutes: int = Field(30, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_enabled: bool = Field(False, alias="AURA_JWT_ENABLED")
    mcp_api_key: Optional[str] = Field(None, alias="MCP_API_KEY")

    @field_validator("jwt_enabled", mode="after")
    @classmethod
    def _require_jwt_for_tenant_isolation_in_production(cls, v, info):
        # Tenant data isolation is ENFORCED only when the JWT middleware is
        # active: current_workspace_id() scopes every data store to the
        # caller's org_id taken from request.state.user, which JWTAuthMiddleware
        # sets only when AURA_JWT_ENABLED=true. With it false, every request
        # collapses to the shared 'default' tenant — a cross-tenant read/write
        # hole. Acceptable for single-user dev; a silent data breach in prod.
        # Fail loud at startup, parallel to _reject_open_auth_in_production.
        env = info.data.get("environment", "development")
        if env.lower() == "production" and not v:
            raise ValueError(
                "AURA_JWT_ENABLED must be true in production. Without it, "
                "tenant isolation is not enforced and all callers share the "
                "'default' workspace (cross-tenant data exposure)."
            )
        return v

    # ── Object storage (S45) ────────────────────────────────────────────
    storage_backend: str = Field("local", alias="AURA_STORAGE_BACKEND")
    s3_bucket: Optional[str] = Field(None, alias="AURA_S3_BUCKET")
    s3_endpoint_url: Optional[str] = Field(None, alias="AURA_S3_ENDPOINT_URL")
    s3_region: str = Field("us-east-1", alias="AURA_S3_REGION")
    s3_access_key_id: Optional[str] = Field(
        None, validation_alias=AliasChoices("AURA_S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
    )
    s3_secret_access_key: Optional[str] = Field(
        None, validation_alias=AliasChoices("AURA_S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
    )
    # canonical: "path" (MinIO/most on-prem) | "vhost" (AWS). S3Backend maps
    # to boto3 addressing_style (vhost->virtual) and passes verbatim to DuckDB.
    s3_url_style: str = Field("path", alias="AURA_S3_URL_STYLE")
    s3_prefix: str = Field("uploads", alias="AURA_S3_PREFIX")
    s3_use_ssl: bool = Field(True, alias="AURA_S3_USE_SSL")

    @model_validator(mode="after")
    def _require_bucket_for_s3(self):
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("AURA_S3_BUCKET must be set when AURA_STORAGE_BACKEND=s3.")
        return self

    # ── Primary Database (Connector / Sandbox default) ──────────────────
    db_host: str = Field("localhost", alias="DB_HOST")
    db_port: int = Field(5432, alias="DB_PORT")
    db_name: str = Field("aura_vault", alias="DB_NAME")
    db_user: str = Field("postgres", alias="DB_USER")
    db_password: str = Field("", alias="DB_PASSWORD")
    db_type: str = Field("postgresql", alias="DB_TYPE")
    db_pool_size: int = Field(10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(20, alias="DB_MAX_OVERFLOW")
    database_url: str = Field("sqlite:///./aura.db", alias="DATABASE_URL")

    # ── AURA Vault (Hybrid Multimodal DB) ───────────────────────────────
    vault_backend: str = Field("postgresql", alias="AURA_VAULT_BACKEND")
    vault_host: str = Field("localhost", alias="AURA_VAULT_HOST")
    vault_port: int = Field(5432, alias="AURA_VAULT_PORT")
    vault_database: str = Field("aura_vault", alias="AURA_VAULT_DATABASE")
    vault_user: str = Field("postgres", alias="AURA_VAULT_USER")
    vault_password: str = Field("", alias="AURA_VAULT_PASSWORD")
    vault_duckdb_path: str = Field(":memory:", alias="AURA_VAULT_DUCKDB_PATH")

    # ── Scheduler ───────────────────────────────────────────────────────
    scheduler_database_url: str = Field(
        "sqlite+aiosqlite:///data/scheduler.db",
        alias="SCHEDULER_DATABASE_URL",
    )
    scheduler_check_interval: int = Field(60, alias="SCHEDULER_CHECK_INTERVAL")

    # ── Execution ───────────────────────────────────────────────────────
    execution_timeout: float = Field(15.0, alias="EXECUTION_TIMEOUT_SECONDS")

    # ── Orchestration ───────────────────────────────────────────────────
    tiny_recursive_max_depth: int = Field(3, alias="TINY_RECURSIVE_MAX_DEPTH")
    tiny_recursive_confidence: float = Field(0.8, alias="TINY_RECURSIVE_CONFIDENCE")

    # ── Performance ─────────────────────────────────────────────────────
    request_timeout: int = Field(30, alias="REQUEST_TIMEOUT")
    max_concurrent_requests: int = Field(100, alias="MAX_CONCURRENT_REQUESTS")

    # ── Feature Flags ───────────────────────────────────────────────────
    enable_ai_chat: bool = Field(True, alias="ENABLE_AI_CHAT")
    enable_database_connectivity: bool = Field(True, alias="ENABLE_DATABASE_CONNECTIVITY")
    enable_visualization: bool = Field(True, alias="ENABLE_VISUALIZATION")
    enable_strategic_mode: bool = Field(True, alias="ENABLE_STRATEGIC_MODE")

    # ── Metadata Admin ──────────────────────────────────────────────────
    admin_email: Optional[str] = Field(None, alias="AURA_ADMIN_EMAIL")

    # ── API Key Auth (opt-in) ───────────────────────────────────────────
    api_key: Optional[str] = Field(None, alias="AURA_API_KEY")

    # ── Rate Limiting ───────────────────────────────────────────────────
    rate_limit_enabled: bool = Field(True, alias="AURA_RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(100, alias="AURA_RATE_LIMIT_REQUESTS")
    rate_limit_window_seconds: int = Field(60, alias="AURA_RATE_LIMIT_WINDOW_SECONDS")
    redis_url: Optional[str] = Field(None, alias="AURA_REDIS_URL")

    model_config = {
        "env_file": _locate_env_files(),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }

    # ── Derived helpers ─────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def vault_dsn(self) -> str:
        return (
            f"postgresql://{self.vault_user}:{self.vault_password}"
            f"@{self.vault_host}:{self.vault_port}/{self.vault_database}"
        )

    @property
    def db_dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache()
def get_settings() -> AuraSettings:
    """Return a cached singleton of the settings."""
    return AuraSettings()


def reload_settings() -> AuraSettings:
    """Rebuild the cached Settings from the current environment (tests/S45)."""
    get_settings.cache_clear()
    return get_settings()


# Convenience alias — ``from shared.config import settings``
settings = get_settings()
