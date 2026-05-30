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
# Provider-reported token usage capture (Step 2 of BATS refactor)
# ────────────────────────────────────────────────────────────────────
# Each concrete provider populates the contextvar IMMEDIATELY before
# returning a string from .generate(). The cached/observer boundary
# reads it back once per call. Three small helpers shield the call
# sites from version drift in the Groq/OpenAI/Gemini SDK shapes.

def _capture_usage_from_response(response: Any, *, provider: str, model: str, finish_reason: str = "") -> None:
    """Capture from an OpenAI/Groq SDK response object (``response.usage``)."""
    try:
        from shared.llm_token_usage import TokenUsage, set_last_usage
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        set_last_usage(TokenUsage(
            prompt_tokens=prompt, completion_tokens=completion,
            source="provider_reported", provider=provider, model=model,
            finish_reason=finish_reason or "",
        ))
    except Exception as exc:  # pragma: no cover — instrumentation must never break gen
        logger.debug("usage capture (response) failed: %s", exc)


def _capture_usage_from_dict(usage_dict: Dict[str, Any], *, provider: str, model: str, finish_reason: str = "") -> None:
    """Capture from a raw httpx JSON payload's ``usage`` dict."""
    try:
        from shared.llm_token_usage import TokenUsage, set_last_usage
        if not usage_dict:
            return
        set_last_usage(TokenUsage(
            prompt_tokens=int(usage_dict.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_dict.get("completion_tokens", 0) or 0),
            source="provider_reported", provider=provider, model=model,
            finish_reason=finish_reason or "",
        ))
    except Exception as exc:  # pragma: no cover
        logger.debug("usage capture (dict) failed: %s", exc)


def _capture_usage_explicit(*, prompt_tokens: int, completion_tokens: int,
                            provider: str, model: str, finish_reason: str = "") -> None:
    """Capture pre-extracted token counts (used by Gemini's translated metadata)."""
    try:
        from shared.llm_token_usage import TokenUsage, set_last_usage
        set_last_usage(TokenUsage(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            source="provider_reported", provider=provider, model=model,
            finish_reason=finish_reason or "",
        ))
    except Exception as exc:  # pragma: no cover
        logger.debug("usage capture (explicit) failed: %s", exc)


def _gemini_finish_reason(response: Any) -> str:
    """Translate Gemini's enum-ish finish_reason onto the OpenAI vocabulary
    (``stop`` / ``length`` / ``safety`` / ``other``) so Step 6 eval gates
    can assert one canonical word regardless of provider."""
    try:
        cands = getattr(response, "candidates", None) or []
        if not cands:
            return ""
        raw = getattr(cands[0], "finish_reason", "")
        # Gemini SDK exposes either a string or an int enum depending on
        # version; normalise via .name when possible, then lower-case.
        name = getattr(raw, "name", str(raw)).lower()
        return {
            "stop": "stop", "max_tokens": "length", "length": "length",
            "safety": "safety", "recitation": "safety",
        }.get(name, name)
    except Exception:
        return ""

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

    @staticmethod
    def _build_messages(prompt: Union[str, List[str]]) -> List[Dict[str, str]]:
        """Convert a prompt (string or list) to OpenAI-style messages.

        - Single string → one user message.
        - List → first element is system, rest are user messages.
        """
        if isinstance(prompt, list):
            msgs: List[Dict[str, str]] = []
            if prompt:
                msgs.append({"role": "system", "content": prompt[0]})
            for p in prompt[1:]:
                msgs.append({"role": "user", "content": p})
            # If only a system prompt was given, also add it as user
            if len(msgs) == 1:
                msgs.append({"role": "user", "content": prompt[0]})
            return msgs
        return [{"role": "user", "content": prompt}]

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

        messages = self._build_messages(prompt)

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

    def _generate_via_sdk(self, messages: list, temperature: float, max_tokens: int) -> Optional[str]:
        """Call the Groq Python SDK."""
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = response.choices[0] if response.choices else None
            if choice and choice.message:
                _capture_usage_from_response(response, provider="groq", model=self.model,
                                             finish_reason=getattr(choice, "finish_reason", ""))
                return (choice.message.content or "").strip()
        except Exception as exc:
            exc_str = str(exc).lower()
            if any(kw in exc_str for kw in ("rate", "too large", "413", "limit")):
                raise LLMRateLimitError(f"Groq rate/size limit: {exc}") from exc
            logger.warning("Groq SDK generation failed: %s", exc)
        return None

    def _generate_via_httpx(self, messages: list, temperature: float, max_tokens: int) -> Optional[str]:
        """Fallback: raw httpx call to Groq's OpenAI-compatible API."""
        import httpx
        try:
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
                payload = resp.json()
                choices = payload.get("choices", [])
                if choices:
                    _capture_usage_from_dict(
                        payload.get("usage", {}),
                        provider="groq",
                        model=self.model,
                        finish_reason=choices[0].get("finish_reason", ""),
                    )
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

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        if not self.is_available():
            return None

        messages = self._build_messages(prompt)
        temperature = kwargs.get("temperature", 0.2)
        max_tokens = kwargs.get("max_tokens", _DEFAULT_MAX_TOKENS)

        if self._client != "httpx":
            result = self._generate_via_sdk(messages, temperature, max_tokens)
            if result is not None:
                return result

        return self._generate_via_httpx(messages, temperature, max_tokens)


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
            # Gemini's usage_metadata uses different field names than
            # OpenAI/Groq — translate so observers see one shape.
            meta = getattr(response, "usage_metadata", None)
            if meta is not None:
                _capture_usage_explicit(
                    prompt_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
                    completion_tokens=int(getattr(meta, "candidates_token_count", 0) or 0),
                    provider="gemini",
                    model=self.model,
                    finish_reason=_gemini_finish_reason(response),
                )
            return text.strip() if text else None
        except Exception as exc:
            # Detect rate-limit / quota errors so callers can distinguish
            # "you need to add billing" from "LLM hallucinated empty".
            # Previously this was swallowed into None — users saw "empty
            # SQL response" with no hint that the root cause was a 429.
            msg = str(exc)
            logger.warning("Gemini generation failed: %s", msg)
            lower = msg.lower()
            if "429" in msg or "quota" in lower or "rate" in lower or "resource_exhausted" in lower:
                raise LLMRateLimitError(f"Gemini rate-limit / quota: {msg}") from exc
            # Re-raise so the calling agent surfaces the real error
            # instead of degrading to a generic "empty response" path.
            raise


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
            messages = self._build_messages(prompt)
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.2),
                max_tokens=kwargs.get("max_tokens", _DEFAULT_MAX_TOKENS),
            )
            choice = response.choices[0] if response.choices else None
            if choice and choice.message:
                _capture_usage_from_response(
                    response, provider="openai", model=self.model,
                    finish_reason=getattr(choice, "finish_reason", ""),
                )
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
        last_exc: Optional[Exception] = None
        for provider in self._providers:
            try:
                result = provider.generate(prompt, **kwargs)
                if result is not None:
                    return result
            except LLMRateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "Provider %s hit rate/size limit, falling back to next provider…",
                    provider.provider_name,
                )
                continue
            except Exception as exc:
                # Per-provider generation errors must not abort the
                # fallback chain — record and try the next provider.
                # If every provider raises, re-raise the last one so the
                # caller knows the real reason (was previously silently
                # turning into None → "empty SQL response").
                last_exc = exc
                logger.warning(
                    "Provider %s failed (%s), falling back to next provider…",
                    provider.provider_name, exc,
                )
                continue
        if last_exc is not None:
            raise last_exc
        return None

    def generate_json(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[Dict[str, Any]]:
        last_exc: Optional[Exception] = None
        for provider in self._providers:
            try:
                result = provider.generate_json(prompt, **kwargs)
                if result is not None:
                    return result
            except LLMRateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "Provider %s hit rate/size limit, falling back to next provider…",
                    provider.provider_name,
                )
                continue
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Provider %s generate_json failed (%s), falling back…",
                    provider.provider_name, exc,
                )
                continue
        if last_exc is not None:
            raise last_exc
        return None


# ────────────────────────────────────────────────────────────────────
# LLM call observers (Prometheus / BATS / Audit)
# ────────────────────────────────────────────────────────────────────
# Each observer is independently isolated: a crash in one no longer
# suppresses the others' bookkeeping. _build_call_ctx resolves the
# real provider name (unwrapping _FallbackProvider) and prefers
# provider-reported usage over the 4-chars/token estimate.

class _LLMCallContext:
    __slots__ = (
        "provider", "model", "prompt", "result", "cached",
        "prompt_tokens", "completion_tokens", "usage_source", "finish_reason",
    )

    def __init__(self, provider: str, model: str, prompt: Union[str, List[str]],
                 result: Optional[str], cached: bool, prompt_tokens: int,
                 completion_tokens: int, usage_source: str, finish_reason: str) -> None:
        self.provider = provider
        self.model = model
        self.prompt = prompt
        self.result = result
        self.cached = cached
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.usage_source = usage_source
        self.finish_reason = finish_reason

    @property
    def prompt_text(self) -> str:
        return self.prompt if isinstance(self.prompt, str) else "\n".join(self.prompt)


def _build_call_ctx(inner: LLMProvider, prompt: Union[str, List[str]],
                    result: Optional[str], cached: bool) -> _LLMCallContext:
    """Resolve provider name + token counts. Real usage from the provider
    contextvar wins; falls back to estimation only when missing (cache
    hits, mock LLMs, error paths where the provider couldn't surface usage)."""
    from shared.llm_cache import estimate_request_tokens, estimate_tokens
    from shared.llm_token_usage import clear_last_usage, get_last_usage

    provider_name = getattr(inner, "provider_name", "unknown")
    inner_primary = getattr(inner, "_primary", None)
    if inner_primary is not None:
        provider_name = getattr(inner_primary, "provider_name", provider_name)
    model = inner.model or ""

    usage = get_last_usage()
    # Always clear so a stale value can't leak into a downstream call's
    # bookkeeping (e.g. when a cache hit follows a real call).
    clear_last_usage()

    if usage is not None and usage.source == "provider_reported":
        return _LLMCallContext(
            provider=usage.provider or provider_name,
            model=usage.model or model,
            prompt=prompt, result=result, cached=cached,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            usage_source="provider_reported",
            finish_reason=usage.finish_reason,
        )

    return _LLMCallContext(
        provider=provider_name, model=model, prompt=prompt, result=result, cached=cached,
        prompt_tokens=estimate_request_tokens(prompt),
        completion_tokens=estimate_tokens(result or ""),
        usage_source="estimated",
        finish_reason="",
    )


class _PrometheusObserver:
    """Emits LLM_TOKENS counters for the cost dashboard (Step S6.2)."""

    def on_call(self, ctx: _LLMCallContext) -> None:
        from shared.observability import LLM_TOKENS
        LLM_TOKENS.labels(provider=ctx.provider, model=ctx.model, kind="prompt").inc(ctx.prompt_tokens)
        LLM_TOKENS.labels(provider=ctx.provider, model=ctx.model, kind="completion").inc(ctx.completion_tokens)
        if ctx.cached:
            LLM_TOKENS.labels(provider=ctx.provider, model=ctx.model, kind="cached_completion").inc(ctx.completion_tokens)


class _BudgetObserver:
    """Debits the BATS per-session pool with REAL provider-reported tokens
    when available. Cache hits charge only the prompt (we sent it to the
    cache-key hasher) and skip the completion charge."""

    def on_call(self, ctx: _LLMCallContext) -> None:
        from shared.budget import consume_tokens_from_current
        completion_charge = 0 if ctx.cached else ctx.completion_tokens
        consume_tokens_from_current(ctx.prompt_tokens + completion_charge)


class _AuditObserver:
    """Appends every LLM call to the immutable hash-chained audit log
    (no-op unless AURA_AUDIT_ENABLED=true). Step 7 / TRAIGA control."""

    def on_call(self, ctx: _LLMCallContext) -> None:
        from shared.audit_log import audit_prompt
        audit_prompt(
            provider=ctx.provider, model=ctx.model,
            prompt=ctx.prompt_text, response=ctx.result, cached=ctx.cached,
        )


_OBSERVERS = [_PrometheusObserver(), _BudgetObserver(), _AuditObserver()]


def register_llm_observer(observer: Any) -> None:
    """Attach a custom observer (must implement ``on_call(ctx)``).

    Useful for tests that need to assert on real token counts without
    spelunking through Prometheus, and for downstream apps that want to
    forward LLM events to their own telemetry pipeline.
    """
    _OBSERVERS.append(observer)


class _CachedProvider(LLMProvider):
    """Wraps an inner provider with content-addressable response caching and
    a token-budget guardrail. Bypasses the cache when the caller asks for
    high-temperature output (creativity beats deduplication)."""

    provider_name = "cached"

    def __init__(self, inner: LLMProvider) -> None:
        super().__init__(model=inner.model)
        self._inner = inner

    def is_available(self) -> bool:
        return self._inner.is_available()

    def _check_budget(self, prompt: Union[str, List[str]]) -> None:
        from shared.llm_cache import MAX_TOKENS_PER_REQUEST, estimate_request_tokens
        tokens = estimate_request_tokens(prompt)
        if tokens > MAX_TOKENS_PER_REQUEST:
            raise LLMRateLimitError(
                f"Prompt exceeds AURA_MAX_TOKENS_PER_REQUEST "
                f"({tokens} > {MAX_TOKENS_PER_REQUEST}). Trim context and retry."
            )

    def _record_tokens(self, prompt: Union[str, List[str]], result: Optional[str], cached: bool) -> None:
        """Notify each registered LLM observer with the call context.

        Discrete observers replace the prior bundled try/except — a failure
        in (e.g.) the audit observer no longer suppresses Prometheus
        counters or BATS debits. Each observer logs its own traceback so a
        silent gap in one stream is investigable. Never raises.
        """
        try:
            ctx = _build_call_ctx(self._inner, prompt, result, cached)
        except Exception:
            logger.exception("LLM observer ctx build failed; bookkeeping skipped")
            return
        for obs in _OBSERVERS:
            try:
                obs.on_call(ctx)
            except Exception:
                logger.exception("LLM observer %s failed", obs.__class__.__name__)

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        from shared.llm_cache import cache_key, is_cacheable_temperature, response_cache
        self._check_budget(prompt)
        if not is_cacheable_temperature(kwargs):
            result = self._inner.generate(prompt, **kwargs)
            self._record_tokens(prompt, result, cached=False)
            return result
        key = cache_key(
            getattr(self._inner, "provider_name", "unknown"),
            self._inner.model or "",
            prompt,
            kwargs,
        )
        hit = response_cache.get(key)
        if hit is not None:
            logger.debug("LLM cache hit (%s)", key[:16])
            self._record_tokens(prompt, hit, cached=True)
            return hit
        result = self._inner.generate(prompt, **kwargs)
        if result:
            response_cache.set(key, result)
        self._record_tokens(prompt, result, cached=False)
        return result


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
            inst = _CachedProvider(cls(model=model, **kwargs))
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
                " -> ".join(p.provider_name for p in available),
            )
        # Wrap in a fallback provider so rate-limit errors cascade to next,
        # then in a cached provider so identical prompts skip the wire.
        result_llm = _CachedProvider(_FallbackProvider(primary, available))
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
    fallback = _CachedProvider(OllamaProvider(model=model))
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
