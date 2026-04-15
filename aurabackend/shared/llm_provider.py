"""
LLM Provider Abstraction
========================
Provider-agnostic wrapper so AURA is never locked to a single AI vendor.

Supported providers (in auto-detect priority order):
  1. **Groq**     — free cloud, fastest inference. Requires GROQ_API_KEY.
  2. **Gemini**   — Google cloud API.  Requires GEMINI_API_KEY or GOOGLE_API_KEY.
  3. **Ollama**   — local, free, no API key.  Best for air-gapped / self-hosted.
  4. **OpenAI**   — OpenAI cloud API.  Requires OPENAI_API_KEY.

Usage:
    from shared.llm_provider import get_llm

    llm = get_llm()                          # auto-picks best available
    llm = get_llm(provider="groq")           # force specific provider
    llm = get_llm(provider="gemini", model="gemini-2.5-flash")

    text = llm.generate("Explain CTEs in SQL")
    text = llm.generate(["system prompt", "user message"])
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = int(os.getenv("AURA_DEFAULT_MAX_TOKENS", "4096"))
_OLLAMA_HEALTH_TIMEOUT = int(os.getenv("OLLAMA_HEALTH_TIMEOUT", "3"))


class LLMRateLimitError(Exception):
    """Raised when the LLM provider rejects the request due to rate/size limits."""
    pass


def _setting(attr: str) -> Optional[str]:
    """Read a value from shared.config.settings (which loads .env files).
    Returns None if unavailable or empty."""
    try:
        from shared.config import settings
        val = getattr(settings, attr, None)
        return val if val else None
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────
# Base class
# ────────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Abstract interface every LLM backend must implement."""

    provider_name: str = "base"

    def __init__(self, model: str = "", **kwargs: Any) -> None:
        self.model = model
        self._kwargs = kwargs

    @abstractmethod
    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        """Send prompt(s) to the LLM and return the text response (or None)."""
        ...

    def generate_json(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Convenience: generate and parse as JSON."""
        text = self.generate(prompt, **kwargs)
        logger.debug("LLM raw response (len=%d)", len(text) if text else 0)
        if text is None:
            return None
        # Strip markdown code fences
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider is configured and reachable."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} model={self.model!r}>"


# ────────────────────────────────────────────────────────────────────
# Ollama provider  (local, free, no API key)
# ────────────────────────────────────────────────────────────────────

class OllamaProvider(LLMProvider):
    """
    Talks to a local Ollama server over HTTP.
    Install: https://ollama.com   →  ollama pull llama3
    Default URL: http://localhost:11434
    """

    provider_name = "ollama"

    # Good default models for data engineering tasks (in preference order)
    _DEFAULT_MODELS = os.getenv(
        "OLLAMA_PREFERRED_MODELS",
        "qwen2.5-coder:7b,llama3:8b,mistral:7b,phi3:mini,deepseek-coder-v2:16b",
    ).split(",")

    def __init__(self, model: str = "", **kwargs: Any) -> None:
        self._base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        super().__init__(model=model or os.getenv("OLLAMA_MODEL", ""), **kwargs)
        self._resolved_model: Optional[str] = None

    def _pick_model(self) -> Optional[str]:
        """Return the first locally-available model, or None."""
        if self._resolved_model:
            return self._resolved_model

        if self.model:
            self._resolved_model = self.model
            return self.model

        try:
            import httpx
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=_OLLAMA_HEALTH_TIMEOUT)
            if resp.status_code == 200:
                available = {m["name"] for m in resp.json().get("models", [])}
                # Normalise: "llama3:8b" might appear as "llama3:8b" or just "llama3"
                for candidate in self._DEFAULT_MODELS:
                    if candidate in available:
                        self._resolved_model = candidate
                        return candidate
                    # Try without tag
                    base_name = candidate.split(":")[0]
                    for av in available:
                        if av.startswith(base_name):
                            self._resolved_model = av
                            return av
                # If none of our preferred models, take any available
                if available:
                    self._resolved_model = next(iter(available))
                    return self._resolved_model
        except Exception:
            pass
        return None

    def is_available(self) -> bool:
        try:
            import httpx
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=_OLLAMA_HEALTH_TIMEOUT)
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            return len(models) > 0
        except Exception:
            return False

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        model = self._pick_model()
        if not model:
            return None

        # Build the message(s)
        if isinstance(prompt, list):
            # Treat first as system, rest as user
            system_msg = prompt[0] if prompt else ""
            user_msg = "\n\n".join(prompt[1:]) if len(prompt) > 1 else ""
            messages = []
            if system_msg:
                messages.append({"role": "system", "content": system_msg})
            if user_msg:
                messages.append({"role": "user", "content": user_msg})
            else:
                messages.append({"role": "user", "content": system_msg})
        else:
            messages = [{"role": "user", "content": prompt}]

        try:
            import httpx
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": kwargs.get("temperature", 0.2),
                    "num_predict": kwargs.get("max_tokens", _DEFAULT_MAX_TOKENS),
                },
            }
            timeout = float(os.getenv("AURA_LLM_TIMEOUT", "120"))
            resp = httpx.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                return content.strip() if content else None
        except Exception as exc:
            logger.warning("Ollama generation failed: %s", exc)
        return None


# ────────────────────────────────────────────────────────────────────
# Groq provider  (free cloud, fastest inference)
# ────────────────────────────────────────────────────────────────────

class GroqProvider(LLMProvider):
    """Calls the Groq API (OpenAI-compatible). Free tier: 30 RPM, 14.4k RPD."""

    provider_name = "groq"

    def __init__(self, model: str = "", **kwargs: Any) -> None:
        default_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        super().__init__(model=model or default_model, **kwargs)
        self._api_key = os.getenv("GROQ_API_KEY") or _setting("groq_api_key")
        self._base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        self._client: Any = None
        self._init()

    def _init(self) -> None:
        if not self._api_key:
            return
        try:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        except ImportError:
            # Fallback: use httpx directly (no extra dependency needed)
            self._client = "httpx"
        except Exception as exc:
            logger.warning("Groq init failed: %s", exc)

    def is_available(self) -> bool:
        return self._client is not None and self._api_key is not None

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        if not self.is_available():
            return None

        # Build messages
        if isinstance(prompt, list):
            messages = [{"role": "system", "content": prompt[0]}]
            for p in prompt[1:]:
                messages.append({"role": "user", "content": p})
        else:
            messages = [{"role": "user", "content": prompt}]

        temperature = kwargs.get("temperature", 0.2)
        max_tokens = kwargs.get("max_tokens", _DEFAULT_MAX_TOKENS)

        # Try native groq SDK first
        if self._client != "httpx":
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                choice = response.choices[0] if response.choices else None
                if choice and choice.message:
                    return (choice.message.content or "").strip()
            except Exception as exc:
                exc_str = str(exc).lower()
                if "rate" in exc_str or "too large" in exc_str or "413" in exc_str or "limit" in exc_str:
                    raise LLMRateLimitError(f"Groq rate/size limit: {exc}") from exc
                logger.warning("Groq SDK generation failed: %s", exc)
                return None

        # Fallback: raw httpx call (OpenAI-compatible API)
        try:
            import httpx
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=float(os.getenv("AURA_LLM_TIMEOUT", "120")),
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return (choices[0].get("message", {}).get("content", "") or "").strip()
            elif resp.status_code in (413, 429):
                raise LLMRateLimitError(
                    f"Groq HTTP {resp.status_code}: prompt too large or rate limit exceeded. "
                    f"Try a simpler prompt or wait a moment."
                )
            else:
                logger.warning("Groq HTTP %d: %s", resp.status_code, resp.text[:200])
        except LLMRateLimitError:
            raise
        except Exception as exc:
            logger.warning("Groq httpx generation failed: %s", exc)
        return None


# ────────────────────────────────────────────────────────────────────
# Gemini provider  (Google cloud)
# ────────────────────────────────────────────────────────────────────

class GeminiProvider(LLMProvider):
    """Wraps the google-generativeai SDK."""

    provider_name = "gemini"

    def __init__(self, model: str = "", **kwargs: Any) -> None:
        default_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        super().__init__(model=model or default_model, **kwargs)
        self._api_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or _setting("gemini_api_key")
        )
        self._genai_model: Any = None
        self._init()

    def _init(self) -> None:
        if not self._api_key:
            return
        try:
            import google.generativeai as genai
            configure_fn = getattr(genai, "configure", None)
            if callable(configure_fn):
                configure_fn(api_key=self._api_key)
            model_cls = getattr(genai, "GenerativeModel", None)
            if model_cls:
                gen_cfg = self._kwargs.get("generation_config")
                if gen_cfg:
                    self._genai_model = model_cls(self.model, generation_config=gen_cfg)
                else:
                    self._genai_model = model_cls(self.model)
        except Exception as exc:
            logger.warning("Gemini init failed: %s", exc)

    def is_available(self) -> bool:
        return self._genai_model is not None

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        if not self._genai_model:
            return None
        try:
            if isinstance(prompt, list):
                response = self._genai_model.generate_content(prompt)
            else:
                response = self._genai_model.generate_content(prompt)
            text = getattr(response, "text", None) or ""
            return text.strip() if text else None
        except Exception as exc:
            logger.warning("Gemini generation failed: %s", exc)
            return None


# ────────────────────────────────────────────────────────────────────
# OpenAI provider  (cloud)
# ────────────────────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    """Wraps the openai Python SDK (also works with Azure OpenAI)."""

    provider_name = "openai"

    def __init__(self, model: str = "", **kwargs: Any) -> None:
        default_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        super().__init__(model=model or default_model, **kwargs)
        self._api_key = os.getenv("OPENAI_API_KEY") or _setting("openai_api_key")
        self._client: Any = None
        self._init()

    def _init(self) -> None:
        if not self._api_key:
            return
        try:
            import openai
            self._client = openai.OpenAI(api_key=self._api_key)
        except Exception as exc:
            logger.warning("OpenAI init failed: %s", exc)

    def is_available(self) -> bool:
        return self._client is not None

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        if not self._client:
            return None
        try:
            if isinstance(prompt, list):
                messages = [{"role": "system", "content": prompt[0]}]
                for p in prompt[1:]:
                    messages.append({"role": "user", "content": p})
            else:
                messages = [{"role": "user", "content": prompt}]

            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.2),
                max_tokens=kwargs.get("max_tokens", _DEFAULT_MAX_TOKENS),
            )
            choice = response.choices[0] if response.choices else None
            if choice and choice.message:
                return (choice.message.content or "").strip()
        except Exception as exc:
            logger.warning("OpenAI generation failed: %s", exc)
        return None


# ────────────────────────────────────────────────────────────────────
# Factory / Auto-detect
# ────────────────────────────────────────────────────────────────────

# Provider priority (env-overridable)
_PROVIDER_ORDER = os.getenv("AURA_LLM_PROVIDERS", "groq,gemini,ollama,openai").split(",")

_PROVIDER_MAP: Dict[str, type] = {
    "groq": GroqProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
}

# Singleton cache
_cached_llm: Optional[LLMProvider] = None
_cached_key: Optional[str] = None


class _FallbackProvider(LLMProvider):
    """Wraps multiple providers; automatically falls back on rate/size errors."""

    provider_name = "fallback"

    def __init__(self, primary: LLMProvider, providers: List[LLMProvider]) -> None:
        super().__init__(model=primary.model)
        self._primary = primary
        self._providers = providers  # all available providers in priority order

    def is_available(self) -> bool:
        return self._primary.is_available()

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        for provider in self._providers:
            try:
                result = provider.generate(prompt, **kwargs)
                if result is not None:
                    return result
            except LLMRateLimitError:
                logger.warning(
                    "Provider %s hit rate/size limit, falling back to next provider…",
                    provider.provider_name,
                )
                continue
        return None

    def generate_json(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[Dict[str, Any]]:
        for provider in self._providers:
            try:
                result = provider.generate_json(prompt, **kwargs)
                if result is not None:
                    return result
            except LLMRateLimitError:
                logger.warning(
                    "Provider %s hit rate/size limit, falling back to next provider…",
                    provider.provider_name,
                )
                continue
        return None


def get_llm(
    *,
    provider: Optional[str] = None,
    model: str = "",
    force_new: bool = False,
    **kwargs: Any,
) -> LLMProvider:
    """
    Return the best available LLM provider.

    Args:
        provider: Force a specific provider ("ollama", "gemini", "openai").
        model:    Override the default model name.
        force_new: Skip the singleton cache.
        **kwargs: Extra args passed to the provider constructor.

    Returns:
        An LLMProvider instance. Call .is_available() to check if it's live.
    """
    global _cached_llm, _cached_key

    cache_key = f"{provider}:{model}"
    if not force_new and _cached_llm and _cached_key == cache_key:
        return _cached_llm

    if provider:
        cls = _PROVIDER_MAP.get(provider.lower())
        if cls:
            inst = cls(model=model, **kwargs)
            _cached_llm = inst
            _cached_key = cache_key
            return inst
        raise ValueError(f"Unknown LLM provider: {provider!r}. Available: {list(_PROVIDER_MAP)}")

    # Auto-detect: collect all available providers in priority order
    available: List[LLMProvider] = []
    for name in _PROVIDER_ORDER:
        name = name.strip().lower()
        cls = _PROVIDER_MAP.get(name)
        if not cls:
            continue
        inst = cls(model=model, **kwargs)
        if inst.is_available():
            available.append(inst)

    if available:
        primary = available[0]
        logger.info("AURA LLM: auto-selected %s (model=%s)", primary.provider_name, primary.model)
        if len(available) > 1:
            logger.info(
                "AURA LLM: fallback chain: %s",
                " → ".join(p.provider_name for p in available),
            )
        # Wrap in a fallback provider so rate-limit errors cascade to next
        result_llm = _FallbackProvider(primary, available)
        _cached_llm = result_llm
        _cached_key = cache_key
        return result_llm

    # Nothing available — return a no-op Ollama (will return None for all generates)
    logger.warning(
        "AURA LLM: no provider available. Tried: %s. "
        "Install Ollama (https://ollama.com) for free local AI, "
        "or set GEMINI_API_KEY / OPENAI_API_KEY.",
        _PROVIDER_ORDER,
    )
    fallback = OllamaProvider(model=model)
    _cached_llm = fallback
    _cached_key = cache_key
    return fallback


def available_providers() -> List[Dict[str, Any]]:
    """Return status of all configured providers (for /health or diagnostics)."""
    result = []
    for name in _PROVIDER_MAP:
        cls = _PROVIDER_MAP[name]
        try:
            inst = cls()
            result.append({
                "provider": name,
                "available": inst.is_available(),
                "model": inst.model or "(auto)",
            })
        except Exception:
            result.append({"provider": name, "available": False, "model": ""})
    return result
