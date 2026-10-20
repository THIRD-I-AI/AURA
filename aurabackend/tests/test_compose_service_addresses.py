"""The gateway is told where every service it calls lives, in each stack that runs it.

Closes the class behind BUG-429, 431 and 446: the gateway reads a service address
from the environment with a localhost default, the full-stack compose file never
sets it, and inside the gateway's container localhost is the gateway itself -- so
the call fails, or worse, reports the service down, with no error at start-up.

This reads every ``os.getenv("..._URL")`` / ``alias="..._URL"`` in code the gateway
runs and checks each one is set on the gateway in docker-compose.yml, unless it is
listed below with the reason it may stay unset.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_BACKEND = Path(__file__).resolve().parents[1]
_GATEWAY_CODE = ("api_gateway", "shared", "agents", "counterfactual_service", "pipeline", "connectors")

# Addresses the gateway may run without, and why.
_MAY_BE_UNSET = {
    "AURA_GATEWAY_URL": "the gateway's own address; localhost is right inside its container",
    "AURA_LEDGER_DATABASE_URL": "defaults to a SQLite file on the data volume",
    "DATABASE_URL": "the metadata database; defaults to SQLite",
    "GATEWAY_DATABASE_URL": "the gateway database; defaults to SQLite",
    "SCHEDULER_DATABASE_URL": "the scheduler's database; defaults to SQLite",
    "DATABASE_SERVICE_URL": "read only by the execution sandbox service, not the gateway",
    "AURA_REDIS_URL": "optional cache backend; in-memory without it",
    "AURA_S3_ENDPOINT_URL": "only for the S3 storage backend",
    "AURA_LLM_BASE_URL": "optional override of an AI provider endpoint",
    "GROQ_BASE_URL": "optional override of an AI provider endpoint",
    "OLLAMA_BASE_URL": "optional local AI provider",
    "OPENAI_BASE_URL": "optional override of an AI provider endpoint",
}


def _gateway_reads() -> set:
    text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for d in _GATEWAY_CODE for p in (_BACKEND / d).rglob("*.py") if "__pycache__" not in p.parts
    )
    reads = set(re.findall(r'os\.(?:getenv|environ\.get)\(\s*"([A-Z0-9_]*_URL)"', text))
    reads |= set(re.findall(r'alias="([A-Z0-9_]*_URL)"', text))
    return reads


def _gateway_env(compose: Path) -> set:
    env = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["api_gateway"].get("environment") or {}
    return set(env) if isinstance(env, dict) else {str(e).split("=", 1)[0] for e in env}


def test_every_service_address_the_gateway_reads_is_set_in_the_full_stack():
    reads = _gateway_reads()
    assert {"AURA_UASR_URL", "AURA_SANDBOX_URL"} <= reads, "expected to find the gateway's service addresses"
    missing = sorted(reads - _gateway_env(_BACKEND.parent / "docker-compose.yml") - set(_MAY_BE_UNSET))
    assert missing == [], f"read by the gateway but never set in docker-compose.yml: {missing}"


def test_the_exemptions_are_still_read():
    stale = sorted(set(_MAY_BE_UNSET) - _gateway_reads())
    assert stale == [], f"no longer read by the gateway; drop from _MAY_BE_UNSET: {stale}"
