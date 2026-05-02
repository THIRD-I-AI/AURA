"""
Provider-reported LLM token usage capture.

The Groq, Gemini, and OpenAI APIs all return real ``usage`` blocks on
their responses (prompt_tokens / completion_tokens). Each provider's
``generate()`` populates ``_last_usage`` immediately before returning
the response string; the ``_CachedProvider`` boundary reads it and
hands it to every observer (Prometheus / BATS / Audit), which means
the BATS budget tracker debits **real** tokens instead of the
4-chars-per-token estimate.

When a provider can't surface usage (rare error paths, mock LLMs in
tests), the contextvar holds ``None`` and observers fall back to
``shared.llm_cache.estimate_request_tokens``.

A ContextVar (not threading.local) so values flow into asyncio tasks
spawned via ``asyncio.create_task`` and don't leak across requests.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    source: str = "provider_reported"  # or "estimated"
    provider: str = ""
    model: str = ""
    finish_reason: str = ""             # "stop" | "length" | "" — used by Step 6 eval gate


_last_usage: contextvars.ContextVar[Optional[TokenUsage]] = contextvars.ContextVar(
    "aura_llm_last_usage", default=None
)


def set_last_usage(usage: Optional[TokenUsage]) -> None:
    _last_usage.set(usage)


def get_last_usage() -> Optional[TokenUsage]:
    return _last_usage.get()


def clear_last_usage() -> None:
    _last_usage.set(None)
