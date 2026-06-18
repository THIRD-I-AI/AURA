"""
AURA LLM Provider Tests
=========================
Tests for provider base class, message building, generate_json, individual
provider init/availability, the factory function, and the fallback chain.
All external API calls are mocked.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm_provider import (
    _PROVIDER_MAP,
    GeminiProvider,
    GroqProvider,
    LLMProvider,
    LLMRateLimitError,
    OllamaProvider,
    OpenAIProvider,
    _FallbackProvider,
    available_providers,
    get_llm,
)

# ── Helpers ────────────────────────────────────────────────────────

def _reset_cache():
    import shared.llm_provider as mod
    mod._cached_llm = None
    mod._cached_key = None


# ── Base class / _build_messages ──────────────────────────────────

class TestBuildMessages:
    def test_single_string(self):
        msgs = LLMProvider._build_messages("Hello")
        assert msgs == [{"role": "user", "content": "Hello"}]

    def test_list_system_and_user(self):
        msgs = LLMProvider._build_messages(["System prompt", "User message"])
        assert msgs[0] == {"role": "system", "content": "System prompt"}
        assert msgs[1] == {"role": "user", "content": "User message"}

    def test_list_only_system(self):
        msgs = LLMProvider._build_messages(["System only"])
        assert len(msgs) == 2
        assert msgs[0] == {"role": "system", "content": "System only"}
        assert msgs[1] == {"role": "user", "content": "System only"}

    def test_list_multiple_user(self):
        msgs = LLMProvider._build_messages(["sys", "u1", "u2"])
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "user"

    def test_empty_list(self):
        msgs = LLMProvider._build_messages([])
        assert msgs == []


# ── generate_json ─────────────────────────────────────────────────

class TestGenerateJson:
    def test_valid_json(self):
        class DummyProvider(LLMProvider):
            provider_name = "dummy"
            def generate(self, prompt, **kw):
                return '{"key": "value"}'
            def is_available(self):
                return True

        p = DummyProvider()
        result = p.generate_json("test")
        assert result == {"key": "value"}

    def test_json_with_code_fences(self):
        class DummyProvider(LLMProvider):
            provider_name = "dummy"
            def generate(self, prompt, **kw):
                return '```json\n{"a": 1}\n```'
            def is_available(self):
                return True

        p = DummyProvider()
        result = p.generate_json("test")
        assert result == {"a": 1}

    def test_invalid_json_returns_none(self):
        class DummyProvider(LLMProvider):
            provider_name = "dummy"
            def generate(self, prompt, **kw):
                return "not json at all"
            def is_available(self):
                return True

        p = DummyProvider()
        assert p.generate_json("test") is None

    def test_none_response(self):
        class DummyProvider(LLMProvider):
            provider_name = "dummy"
            def generate(self, prompt, **kw):
                return None
            def is_available(self):
                return True

        p = DummyProvider()
        assert p.generate_json("test") is None


# ── LLMProvider repr ──────────────────────────────────────────────

class TestProviderRepr:
    def test_repr(self):
        class DummyProvider(LLMProvider):
            provider_name = "dummy"
            def generate(self, prompt, **kw):
                return None
            def is_available(self):
                return True

        p = DummyProvider(model="test-model")
        assert "DummyProvider" in repr(p)
        assert "test-model" in repr(p)


# ── OllamaProvider ────────────────────────────────────────────────

class TestOllamaProvider:
    def test_init_defaults(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        p = OllamaProvider()
        assert p._base_url == "http://localhost:11434"
        assert p.provider_name == "ollama"

    def test_init_custom_url(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://custom:9999")
        p = OllamaProvider()
        assert p._base_url == "http://custom:9999"

    def test_is_available_no_server(self):
        p = OllamaProvider()
        with patch("httpx.get", side_effect=ConnectionError("no server")):
            assert p.is_available() is False

    def test_is_available_empty_models(self):
        p = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}
        with patch("httpx.get", return_value=mock_resp):
            assert p.is_available() is False

    def test_is_available_with_models(self):
        p = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "llama3:8b"}]}
        with patch("httpx.get", return_value=mock_resp):
            assert p.is_available() is True

    def test_generate_no_model(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        p = OllamaProvider()
        p._resolved_model = None
        with patch.object(p, "_pick_model", return_value=None):
            assert p.generate("Hello") is None

    def test_generate_success(self):
        p = OllamaProvider(model="llama3")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": "Hello back!"}}
        with patch("httpx.post", return_value=mock_resp):
            result = p.generate("Hi")
        assert result == "Hello back!"

    def test_generate_failure(self):
        p = OllamaProvider(model="llama3")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            assert p.generate("Hi") is None

    def test_pick_model_uses_cached(self):
        p = OllamaProvider()
        p._resolved_model = "cached-model"
        assert p._pick_model() == "cached-model"

    def test_pick_model_explicit_model(self):
        p = OllamaProvider(model="my-model")
        p._resolved_model = None
        assert p._pick_model() == "my-model"

    def test_pick_model_from_server(self):
        p = OllamaProvider()
        p._resolved_model = None
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "llama3:8b"}]}
        with patch("httpx.get", return_value=mock_resp):
            model = p._pick_model()
        assert model == "llama3:8b"


# ── GroqProvider ──────────────────────────────────────────────────

class TestGroqProvider:
    def test_no_api_key(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with patch("shared.llm_provider._setting", return_value=None):
            p = GroqProvider()
        assert p.is_available() is False

    def test_is_available_with_key(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        with patch("shared.llm_provider._setting", return_value=None):
            p = GroqProvider()
        assert p._api_key == "test-key"

    def test_generate_not_available(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with patch("shared.llm_provider._setting", return_value=None):
            p = GroqProvider()
        assert p.generate("Hello") is None

    def test_generate_via_httpx(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "key")
        with patch("shared.llm_provider._setting", return_value=None):
            p = GroqProvider()
        p._client = "httpx"  # force httpx path

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Response text"}}]
        }
        with patch("httpx.post", return_value=mock_resp):
            result = p.generate("Hello")
        assert result == "Response text"

    def test_generate_httpx_rate_limit(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "key")
        with patch("shared.llm_provider._setting", return_value=None):
            p = GroqProvider()
        p._client = "httpx"

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "rate limited"
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(LLMRateLimitError):
                p.generate("Hello")

    def test_generate_via_sdk(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "key")
        with patch("shared.llm_provider._setting", return_value=None):
            p = GroqProvider()

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "SDK response"
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
        p._client = mock_client

        result = p.generate("Hello")
        assert result == "SDK response"

    def test_sdk_rate_limit_raises(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "key")
        with patch("shared.llm_provider._setting", return_value=None):
            p = GroqProvider()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("rate limit exceeded")
        p._client = mock_client

        with pytest.raises(LLMRateLimitError):
            p._generate_via_sdk([], 0.2, 4096)


# ── GeminiProvider ────────────────────────────────────────────────

class TestGeminiProvider:
    def test_no_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with patch("shared.llm_provider._setting", return_value=None):
            p = GeminiProvider()
        assert p.is_available() is False

    def test_generate_not_available(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with patch("shared.llm_provider._setting", return_value=None):
            p = GeminiProvider()
        assert p.generate("Hello") is None


# ── OpenAIProvider ────────────────────────────────────────────────

class TestOpenAIProvider:
    def test_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("AURA_LLM_BASE_URL", raising=False)
        with patch("shared.llm_provider._setting", return_value=None):
            p = OpenAIProvider()
        assert p.is_available() is False

    def test_generate_not_available(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("AURA_LLM_BASE_URL", raising=False)
        with patch("shared.llm_provider._setting", return_value=None):
            p = OpenAIProvider()
        assert p.generate("Hello") is None

    def test_base_url_from_env(self, monkeypatch):
        # An operator pointing AURA at any OpenAI-compatible server (vLLM,
        # LM Studio, Azure, an on-prem box) sets OPENAI_BASE_URL.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AURA_LLM_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8001/v1")
        with patch("shared.llm_provider._setting", return_value=None):
            p = OpenAIProvider()
        assert p._base_url == "http://localhost:8001/v1"

    def test_aura_llm_base_url_alias(self, monkeypatch):
        # AURA_LLM_BASE_URL is the vendor-neutral alias for the same thing.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("AURA_LLM_BASE_URL", "http://infra.internal:9000/v1")
        with patch("shared.llm_provider._setting", return_value=None):
            p = OpenAIProvider()
        assert p._base_url == "http://infra.internal:9000/v1"

    def test_base_url_enables_client_without_api_key(self, monkeypatch):
        # A self-hosted endpoint often needs no key: base_url alone must be
        # enough to build the client and point it at the custom server.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AURA_LLM_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8001/v1")
        fake_openai = MagicMock()
        with patch("shared.llm_provider._setting", return_value=None), \
                patch.dict(sys.modules, {"openai": fake_openai}):
            p = OpenAIProvider()
        assert p.is_available() is True
        _, kwargs = fake_openai.OpenAI.call_args
        assert kwargs["base_url"] == "http://localhost:8001/v1"
        # api key is optional when a base_url is supplied (non-empty placeholder
        # so the SDK constructor, which rejects an empty key, still builds).
        assert kwargs["api_key"]

    def test_no_base_url_omits_base_url_kwarg(self, monkeypatch):
        # Default cloud OpenAI: no base_url kwarg, real api key forwarded.
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("AURA_LLM_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        fake_openai = MagicMock()
        with patch("shared.llm_provider._setting", return_value=None), \
                patch.dict(sys.modules, {"openai": fake_openai}):
            p = OpenAIProvider()
        assert p.is_available() is True
        _, kwargs = fake_openai.OpenAI.call_args
        assert "base_url" not in kwargs
        assert kwargs["api_key"] == "sk-test"


# ── FallbackProvider ──────────────────────────────────────────────

class TestFallbackProvider:
    def _mock_provider(self, name="p1"):
        """Create a MagicMock with the `model` attribute that _FallbackProvider needs."""
        m = MagicMock()
        m.model = "test-model"
        m.provider_name = name
        return m

    def test_generate_uses_first(self):
        p1 = self._mock_provider("p1")
        p1.generate.return_value = "from p1"
        p2 = self._mock_provider("p2")

        fb = _FallbackProvider(p1, [p1, p2])
        assert fb.generate("test") == "from p1"
        p2.generate.assert_not_called()

    def test_generate_falls_back_on_rate_limit(self):
        p1 = self._mock_provider("p1")
        p1.generate.side_effect = LLMRateLimitError("rate limited")
        p2 = self._mock_provider("p2")
        p2.generate.return_value = "from p2"

        fb = _FallbackProvider(p1, [p1, p2])
        assert fb.generate("test") == "from p2"

    def test_generate_all_fail(self):
        p1 = self._mock_provider("p1")
        p1.generate.return_value = None

        fb = _FallbackProvider(p1, [p1])
        assert fb.generate("test") is None

    def test_generate_json_with_fallback(self):
        p1 = self._mock_provider("p1")
        p1.generate_json.side_effect = LLMRateLimitError("limited")
        p2 = self._mock_provider("p2")
        p2.generate_json.return_value = {"ok": True}

        fb = _FallbackProvider(p1, [p1, p2])
        assert fb.generate_json("test") == {"ok": True}

    def test_is_available(self):
        p1 = self._mock_provider("p1")
        p1.is_available.return_value = True
        fb = _FallbackProvider(p1, [p1])
        assert fb.is_available() is True


# ── Factory get_llm ──────────────────────────────────────────────

class TestGetLlm:
    def test_explicit_provider(self):
        _reset_cache()
        with patch("shared.llm_provider._setting", return_value=None):
            p = get_llm(provider="ollama", model="test-model", force_new=True)
        # get_llm wraps every provider in a caching layer; unwrap to assert type
        inner = getattr(p, "_inner", p)
        assert isinstance(inner, OllamaProvider)
        assert p.model == "test-model"

    def test_invalid_provider(self):
        _reset_cache()
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm(provider="nonexistent", force_new=True)

    def test_caching(self):
        _reset_cache()
        with patch("shared.llm_provider._setting", return_value=None):
            p1 = get_llm(provider="ollama", force_new=True)
            p2 = get_llm(provider="ollama")
        assert p1 is p2

    def test_force_new_bypasses_cache(self):
        _reset_cache()
        with patch("shared.llm_provider._setting", return_value=None):
            p1 = get_llm(provider="ollama", force_new=True)
            p2 = get_llm(provider="ollama", force_new=True)
        assert p1 is not p2

    def test_aura_llm_provider_env_switch(self, monkeypatch):
        # A deployment pins its backend with AURA_LLM_PROVIDER instead of
        # relying on auto-detect priority.
        _reset_cache()
        monkeypatch.setenv("AURA_LLM_PROVIDER", "ollama")
        with patch("shared.llm_provider._setting", return_value=None):
            p = get_llm(force_new=True)
        inner = getattr(p, "_inner", p)
        assert isinstance(inner, OllamaProvider)

    def test_aura_llm_provider_env_invalid(self, monkeypatch):
        # A typo in the pinned provider must fail loudly, not silently
        # auto-detect something else.
        _reset_cache()
        monkeypatch.setenv("AURA_LLM_PROVIDER", "bogus")
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm(force_new=True)

    def test_explicit_provider_overrides_env(self, monkeypatch):
        # An explicit call argument wins over the env default.
        _reset_cache()
        monkeypatch.setenv("AURA_LLM_PROVIDER", "bogus")
        with patch("shared.llm_provider._setting", return_value=None):
            p = get_llm(provider="ollama", force_new=True)
        inner = getattr(p, "_inner", p)
        assert isinstance(inner, OllamaProvider)


# ── available_providers ──────────────────────────────────────────

class TestAvailableProviders:
    def test_returns_all_providers(self):
        with patch("shared.llm_provider._setting", return_value=None):
            result = available_providers()
        assert isinstance(result, list)
        names = [r["provider"] for r in result]
        assert "groq" in names
        assert "gemini" in names
        assert "ollama" in names
        assert "openai" in names
        for entry in result:
            assert "available" in entry
            assert "model" in entry


# ── LLMRateLimitError ────────────────────────────────────────────

class TestLLMRateLimitError:
    def test_is_exception(self):
        err = LLMRateLimitError("too many requests")
        assert isinstance(err, Exception)
        assert str(err) == "too many requests"
