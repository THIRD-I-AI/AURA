"""
LLM Response Cache + Token Guardrail
=====================================
Sync-friendly content-addressable cache for LLM responses keyed on
``sha256(provider | model | prompt | extra)``. Skips itself when the
caller asks for high-temperature output (creativity beats deduplication).

Also exposes a token estimator used by the provider boundary to refuse
oversized prompts before paying the network round-trip.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

LLM_CACHE_TTL = int(os.getenv("AURA_LLM_CACHE_TTL", "3600"))
LLM_CACHE_MAX = int(os.getenv("AURA_LLM_CACHE_MAX", "500"))
MAX_TOKENS_PER_REQUEST = int(os.getenv("AURA_MAX_TOKENS_PER_REQUEST", "8000"))
CACHEABLE_TEMPERATURE = float(os.getenv("AURA_LLM_CACHE_MAX_TEMP", "0.5"))


class _SyncTTLCache:
    """Tiny thread-safe TTL cache. We can't reuse shared.cache.InMemoryCache
    because LLM providers are called from sync code paths."""

    def __init__(self, ttl_seconds: int, max_size: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_size
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._store) >= self._max:
                now = time.monotonic()
                expired = [k for k, (e, _) in self._store.items() if now > e]
                for k in expired:
                    del self._store[k]
                if len(self._store) >= self._max:
                    drop = max(1, self._max // 4)
                    for k in list(self._store.keys())[:drop]:
                        del self._store[k]
            self._store[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


response_cache = _SyncTTLCache(ttl_seconds=LLM_CACHE_TTL, max_size=LLM_CACHE_MAX)


def estimate_tokens(text: str) -> int:
    """tiktoken when available, otherwise a 4-chars-per-token approximation."""
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore[import-not-found]
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _prompt_text(prompt: Union[str, List[str]]) -> str:
    if isinstance(prompt, list):
        return "\n".join(str(p) for p in prompt)
    return str(prompt)


def estimate_request_tokens(prompt: Union[str, List[str]]) -> int:
    return estimate_tokens(_prompt_text(prompt))


def cache_key(
    provider: str,
    model: str,
    prompt: Union[str, List[str]],
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload = {
        "p": provider,
        "m": model,
        "prompt": prompt,
        "extra": {k: v for k, v in (extra or {}).items() if k in {"temperature", "max_tokens"}},
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return f"llm:{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


def is_cacheable_temperature(kwargs: Dict[str, Any]) -> bool:
    try:
        return float(kwargs.get("temperature", 0.2)) <= CACHEABLE_TEMPERATURE
    except (TypeError, ValueError):
        return False
