# S34d — PII Tokenization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministic HMAC-keyed PII tokens at egress (correlation without exposure), fail-safe to `[REDACTED]` when unkeyed.

Spec: `docs/superpowers/specs/2026-06-11-s34d-pii-tokenization-design.md`

### Task 1: tokenizer in shared/pii_masking.py

**Files:** Modify `aurabackend/shared/pii_masking.py`. Test `aurabackend/tests/test_pii_tokenization.py` (new).

- [ ] **Step 1:** Failing tests: determinism/distinctness over (tenant, field, value); format `PII-[0-9a-f]{12}`; raw absent; unkeyed → `[REDACTED]`; recursion + non-PII preservation; non-string PII values tokenized via `str()`.
- [ ] **Step 2:** Implement `_pii_token(field, value, context)` (HMAC-SHA256 over `context|field|str(value)`, key `AURA_PII_TOKEN_KEY`), `tokenize_pii(data, *, context="")`, `mask_pii_egress(data, *, context="")` (mode select on key presence).
- [ ] **Step 3:** Run file → green; commit `feat(s34d): HMAC-keyed deterministic PII tokens (fail-safe to redaction)`.

### Task 2: egress call sites

**Files:** Modify `aurabackend/counterfactual_service/financial_report.py` (`client_view`), `aurabackend/counterfactual_service/exception_queue.py` (`pending_exceptions`). Test: extend `test_pii_tokenization.py`.

- [ ] **Step 1:** Failing e2e test: with `AURA_PII_TOKEN_KEY` set, two findings sharing `employee_name` → equal `PII-…` tokens in `client_view` and in `pending_exceptions`; stored artifact keeps raw and verifies; without key both fall back to `[REDACTED]`.
- [ ] **Step 2:** Switch both call sites to `mask_pii_egress(..., context=tenant_id)`.
- [ ] **Step 3:** Run new file + `test_financial_audit.py` + `test_exception_queue.py` + CI-exact ruff; commit `feat(s34d): client_view + exception queue emit correlatable PII tokens`; push; PR; CI watch.
