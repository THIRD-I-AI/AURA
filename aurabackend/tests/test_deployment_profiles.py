"""Subsystem D — deployment profiles (cloud | on-prem / air-gapped).

The on-prem profile is a *secure-deployment* guarantee: an air-gapped install
must use local models only (Ollama) and make ZERO external LLM API calls. The
startup guard fails loud if an external-provider key is configured under
on-prem — the same fail-loud pattern as the production auth/jwt guards — so a
misconfigured air-gapped box can't silently egress prompts to a cloud vendor."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _settings(**env):
    # Pin the external-provider keys empty so the test is hermetic — pydantic
    # otherwise reads GROQ/GEMINI/OPENAI keys from the real os.environ. Init
    # kwargs outrank env vars, so a test that wants a key passes it explicitly.
    from shared.config import AuraSettings
    base = {"GROQ_API_KEY": "", "GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}
    base.update(env)
    return AuraSettings(_env_file=None, SECRET_KEY="x", **base)


def test_default_profile_is_cloud():
    assert _settings().deployment_profile == "cloud"


def test_invalid_profile_rejected():
    with pytest.raises(ValueError, match="AURA_DEPLOYMENT_PROFILE"):
        _settings(AURA_DEPLOYMENT_PROFILE="banana")


def test_onprem_rejects_external_provider_key():
    with pytest.raises(ValueError, match="on-prem"):
        _settings(AURA_DEPLOYMENT_PROFILE="onprem", GROQ_API_KEY="sk-secret")


def test_onprem_rejects_gemini_and_openai_keys():
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        with pytest.raises(ValueError, match="on-prem"):
            _settings(AURA_DEPLOYMENT_PROFILE="onprem", **{key: "x"})


def test_onprem_allows_local_only():
    s = _settings(AURA_DEPLOYMENT_PROFILE="onprem")
    assert s.deployment_profile == "onprem"
    assert s.is_onprem is True


def test_cloud_allows_external_keys():
    s = _settings(AURA_DEPLOYMENT_PROFILE="cloud", GROQ_API_KEY="sk-secret")
    assert s.deployment_profile == "cloud"
    assert s.is_onprem is False
