"""
Unified mock LLM for the AURA test suite.

Replaces the four scattered _MockLLM / _HappyMockLLM / _AlwaysFailLLM /
_RecordingMockLLM classes that previously lived inside individual
test_*.py files. Single class, one shape, one set of behaviours.

Why this matters: the prior mocks bypassed ``shared.llm_token_usage._last_usage``
entirely, so the observer chain (Prometheus / BATS / Audit) saw
``usage_source="estimated"`` for every test call. That meant the test
suite never exercised the real-provider observer path — when a
production change reshaped the contextvar contract, mocks happily kept
returning rows while the real LLM path silently broke.

This unified mock populates ``_last_usage`` with synthetic-but-realistic
``TokenUsage`` (provider="mock", finish_reason="stop" by default) so the
observer chain treats it identically to a real Groq/Gemini call.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Union

from shared.llm_cache import estimate_request_tokens, estimate_tokens
from shared.llm_provider import LLMProvider
from shared.llm_token_usage import TokenUsage, set_last_usage

# ── Response routing ──────────────────────────────────────────────────

ResponseFn = Callable[[str], Optional[str]]
"""Maps a prompt text to a string response, or None to fall through to the next rule."""


@dataclass
class MockRule:
    """One pattern → response rule. ``pattern`` is a regex compiled for
    case-insensitive matching against the joined prompt text."""

    pattern: Pattern[str]
    response: Union[str, ResponseFn]


def _compile(pattern: str) -> Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# ── Default canned responses (covers chat / eval / resilience tests) ──

def _default_rules(table_name: str) -> List[MockRule]:
    """Reasonable defaults for the canonical
    intent → sql → execute → viz → analyse flow. Override per test by
    passing a ``rules=`` list to ``UnifiedMockLLM``."""
    return [
        # IntentAgent: "Determine intent. Conversational vs SQL."
        MockRule(_compile(r"intent.*conversation|conversation.*intent"),
                 json.dumps({"intent": "sql", "message": ""})),
        # VisualizationAgent
        MockRule(_compile(r"chart|visualization|visualize"),
                 json.dumps({
                     "type": "bar", "x": "Product", "y": ["total_revenue"],
                     "title": "Revenue by Product",
                     "reason": "categorical x-axis with one numeric measure",
                 })),
        # SQL explainer (one-line)
        MockRule(_compile(r"explain.*sentence|one plain-english sentence"),
                 "Aggregates revenue by product across the test fixture."),
        # AnalysisAgent: synthesise a narrative answer
        MockRule(_compile(r"data summary|column profile|descriptive statistics"),
                 "Widget B leads on revenue across the sample window."),
        # SQL generator (last because it's the broadest pattern)
        MockRule(_compile(r"\bsql\b|\bselect\b|\bquery\b"),
                 f'SELECT "Product", SUM("Revenue") AS total_revenue '
                 f'FROM "{table_name}" GROUP BY "Product"'),
    ]


# ── The unified mock ──────────────────────────────────────────────────

class UnifiedMockLLM(LLMProvider):
    """Single mock for the entire test suite.

    Configure via:

      * ``rules`` — list of ``MockRule`` (regex → response). First match wins.
      * ``default_response`` — returned when no rule matches. Default ``"{}"``
        so JSON-expecting callers can still parse a falsy result.
      * ``finish_reason`` — what to put on the synthetic TokenUsage. Use
        ``"length"`` to simulate a truncated response (eval gate's L8
        regression check).
      * ``fail_mode`` — when ``"unavailable"``, ``is_available`` returns
        False and ``generate`` returns None (replaces _AlwaysFailLLM's
        is_available=False path). When ``"raise"``, ``generate`` raises
        RuntimeError (the original _AlwaysFailLLM behaviour).
      * ``record`` — when True, every call is appended to ``self.calls``.
    """

    provider_name = "mock"

    def __init__(
        self,
        *,
        table_name: str = "sales",
        rules: Optional[List[MockRule]] = None,
        default_response: str = "{}",
        finish_reason: str = "stop",
        fail_mode: Optional[str] = None,  # "unavailable" | "raise" | None
        record: bool = False,
        model: str = "mock-v1",
    ) -> None:
        super().__init__(model=model)
        self._rules = rules if rules is not None else _default_rules(table_name)
        self._default = default_response
        self._finish_reason = finish_reason
        self._fail_mode = fail_mode
        self._record = record
        self.calls: List[Dict[str, Any]] = []

    # ── LLMProvider contract ─────────────────────────────────────────

    def is_available(self) -> bool:
        return self._fail_mode != "unavailable"

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        if self._fail_mode == "raise":
            raise RuntimeError("simulated provider outage (UnifiedMockLLM fail_mode='raise')")
        if self._fail_mode == "unavailable":
            return None

        text = prompt if isinstance(prompt, str) else "\n".join(prompt)
        result = self._match(text)

        # Populate the provider-reported usage contextvar so the
        # _BudgetObserver / _AuditObserver / _PrometheusObserver chain
        # treats this call identically to a real Groq/Gemini call. The
        # token counts use the same estimator the production code uses
        # for cache hits, so the bookkeeping numbers stay realistic.
        set_last_usage(TokenUsage(
            prompt_tokens=estimate_request_tokens(prompt),
            completion_tokens=estimate_tokens(result or ""),
            source="provider_reported",  # masquerade as a real call
            provider=self.provider_name,
            model=self.model,
            finish_reason=self._finish_reason,
        ))

        if self._record:
            self.calls.append({
                "ts": time.time(),
                "prompt": text,
                "response": result,
                "kwargs": dict(kwargs),
                "finish_reason": self._finish_reason,
            })

        return result

    def generate_json(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[Dict[str, Any]]:
        raw = self.generate(prompt, **kwargs)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    # ── Internal ─────────────────────────────────────────────────────

    def _match(self, text: str) -> str:
        for rule in self._rules:
            if rule.pattern.search(text):
                return rule.response(text) if callable(rule.response) else rule.response
        return self._default


# ── Convenience presets (drop-in replacements for the old classes) ────

def chat_happy_path(table_name: str = "sales", *, finish_reason: str = "stop") -> UnifiedMockLLM:
    """Replacement for the old ``_MockLLM`` / ``_HappyMockLLM`` —
    routes the canonical chat pipeline to a deterministic answer."""
    return UnifiedMockLLM(table_name=table_name, finish_reason=finish_reason)


def chat_unavailable() -> UnifiedMockLLM:
    """Replacement for the old ``_AlwaysFailLLM`` (is_available=False path).
    Production code falls back through the provider chain."""
    return UnifiedMockLLM(fail_mode="unavailable")


def chat_raises() -> UnifiedMockLLM:
    """Replacement for ``_AlwaysFailLLM.generate`` — raises on every call."""
    return UnifiedMockLLM(fail_mode="raise")


def chat_recording(table_name: str = "sales", *, finish_reason: str = "stop") -> UnifiedMockLLM:
    """Replacement for ``_RecordingMockLLM`` — happy-path responses
    plus a ``calls`` list populated with prompt/response/finish_reason."""
    return UnifiedMockLLM(table_name=table_name, finish_reason=finish_reason, record=True)


def install_mock(monkeypatch_or_patch_ctx, llm: UnifiedMockLLM) -> None:
    """Standardised patch site — wraps the unified mock in the same
    ``_CachedProvider`` the production ``get_llm`` returns, then patches
    both ``get_llm`` and ``_cached_llm`` so every agent picks it up.

    The ``_CachedProvider`` wrap is mandatory: it's what runs the
    observer dispatch (BATS / Audit / Prometheus) after each call. A
    bare-mock install was the "old mock-LLM path" — it bypassed observer
    dispatch entirely, so test runs never exercised the production
    instrumentation contract. Wrapping fixes that: the mock's
    ``set_last_usage`` populates the contextvar, and the wrapper's
    ``_record_tokens`` reads it back and dispatches to every observer.
    """
    if not hasattr(monkeypatch_or_patch_ctx, "setattr"):
        raise TypeError(
            "install_mock expected a pytest monkeypatch fixture; "
            "for context-manager style use the unified mock with "
            "your own patch() block directly."
        )
    from shared import llm_cache, llm_provider
    # Clear the module-level response cache between tests. Without this,
    # a prior test that sent the same prompt primes a cache hit; the
    # second test never invokes the inner mock, ``_last_usage`` is never
    # populated, and the observer chain falls back to estimation —
    # exactly the failure mode this unification is meant to eliminate.
    llm_cache.response_cache.clear()
    wrapped = llm_provider._CachedProvider(llm)
    monkeypatch_or_patch_ctx.setattr(llm_provider, "get_llm", lambda *a, **kw: wrapped)
    monkeypatch_or_patch_ctx.setattr(llm_provider, "_cached_llm", wrapped)
