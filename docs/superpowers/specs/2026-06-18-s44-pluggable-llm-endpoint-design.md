# S44 — Pluggable LLM Endpoint Portability

> **Sprint S44 · issue #108.** Part of the deployment-portability effort
> ("deployable in any cloud, semi-cloud, and on-prem"). Items 1–2 of a 4-item
> LLM-portability list; items 3–4 are deferred (no driver yet).

**Goal:** Let an operator run AURA's LLM layer in any of three modes —
cloud API, a customer-hosted OpenAI-compatible endpoint, or fully-local /
air-gapped (Ollama) — by configuration alone, with no code change.

## Favorable starting point

The LLM layer was already provider-agnostic: a `LLMProvider` ABC with Groq /
Gemini / Ollama / OpenAI implementations, env-configured, auto-detected in
`AURA_LLM_PROVIDERS` order with a fallback chain (`shared/llm_provider.py`).
Ollama (local/air-gapped) and a configurable base_url for Groq already
existed. Two gaps remained, both small.

## What this builds (items 1–2)

### 1. Configurable `base_url` on `OpenAIProvider`
`OpenAIProvider` hardcoded `openai.OpenAI(api_key=...)` against
api.openai.com. Now it reads `OPENAI_BASE_URL` (alias `AURA_LLM_BASE_URL`)
into `self._base_url` and, when set, passes it to the SDK — so the same
provider drives any OpenAI-compatible server (vLLM, LM Studio, Azure, a
customer gateway, an on-prem box). A `base_url` alone is sufficient: a
self-hosted endpoint usually needs no key, so the api-key guard becomes
`if not self._api_key and not self._base_url: return`, and the key forwarded
to the SDK falls back to a non-empty placeholder (the SDK rejects an empty
key). The default cloud path is unchanged: no base_url kwarg, real key.

### 2. Explicit `AURA_LLM_PROVIDER` switch
`get_llm` gains one line — `provider = provider or os.getenv("AURA_LLM_PROVIDER")`
ahead of the cache key — so a deployment pins exactly one backend instead of
relying on auto-detect priority. An explicit call argument still wins; an
unknown value raises `ValueError` (fail loud, never silently auto-detect).

## Testing

Extends `tests/test_llm_provider.py` (the existing home for this module):
- `_base_url` is read from `OPENAI_BASE_URL` and from the `AURA_LLM_BASE_URL` alias.
- base_url alone builds the client and points it at the custom endpoint (api key optional).
- default cloud path omits the base_url kwarg and forwards the real key (regression guard).
- `AURA_LLM_PROVIDER` selects the backend; an invalid value raises; an explicit arg overrides the env.

The `openai` SDK stays an **optional lazy import** (not added to
`requirements.txt`); tests inject a fake `openai` via `sys.modules` so they
exercise the config plumbing without the SDK installed (matches CI, where it
isn't).

## Docs

`aurabackend/.env.prod.example` documents the three modes (cloud keys /
OpenAI-compatible endpoint / air-gapped Ollama) and the `AURA_LLM_PROVIDER`
pin plus the `AURA_LLM_PROVIDERS` auto-detect order.

## Out of scope — deferred (no driver yet)
- **Item 3:** air-gapped Ollama pipeline end-to-end validation.
- **Item 4:** document Helm + Compose wiring for the three deploy targets.
