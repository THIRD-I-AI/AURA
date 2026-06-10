# S34b — HITL Exception Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Findings flagged `requires_human_review` become a per-report exception queue; each human approve/override is WORM-logged (`audit_human_override`) AND persisted as its own signed, verifiable artifact.

**Architecture:** `finding_id` assigned under the report signature; new `exception_queue.py` reuses a factored-out `_sign_document` from `financial_report.py`; sidecar `<report_hash>.decisions.json` index for O(1) pending reads; two endpoints + gateway proxies.

**Tech stack:** existing `signing.py` / `persistence.py` / `canonical.py` / `shared.audit_log` / `shared.pii_masking`. Tier A tests with monkeypatched persistence.

Spec: `docs/superpowers/specs/2026-06-10-s34b-hitl-exception-queue-design.md`

---

### Task 1: finding_id under the signature

**Files:** Modify `aurabackend/counterfactual_service/financial_report.py` (build_completion_document), `aurabackend/agents/specialists/financial_auditor.py` (version → 0.2.0). Test `aurabackend/tests/test_exception_queue.py`.

- [ ] **Step 1:** Failing tests: `test_findings_get_deterministic_unique_finding_ids` (same inputs → same ids; duplicate finding content → distinct ids), `test_finding_ids_are_signed` (mutating one id breaks `verify_report`).
- [ ] **Step 2:** Implement: in `build_completion_document`, findings become `[{**_as_dict(f), "finding_id": sha256(canonical_dumps({"i": i, "finding": _as_dict(f)})).hexdigest()} for i, f ...]`; bump `FINANCIAL_AUDITOR_VERSION = "0.2.0"`.
- [ ] **Step 3:** Run `tests/test_exception_queue.py tests/test_financial_audit.py` — all pass.
- [ ] **Step 4:** Commit `feat(s34b): finding_id assigned under the report signature`.

### Task 2: factor `_sign_document` out of `sign_and_persist`

**Files:** Modify `financial_report.py`. Tests: existing `test_financial_audit.py` must stay green (behavioral no-op).

- [ ] **Step 1:** Extract `_sign_document(doc) -> stored` (canonical → hash → sign-unless-revoked → persist; no audit_event). `sign_and_persist` = `_sign_document` + existing `audit_event`.
- [ ] **Step 2:** Run `tests/test_financial_audit.py` — 10 pass unchanged.
- [ ] **Step 3:** Commit `refactor(s34b): extract _sign_document for reuse by decision records`.

### Task 3: exception_queue module

**Files:** Create `aurabackend/counterfactual_service/exception_queue.py`. Test `tests/test_exception_queue.py`.

- [ ] **Step 1:** Failing tests: pending lists only `requires_human_review` findings (redacted evidence); decision → signed artifact verifies via `verify_report`, WORM `audit_human_override` called with (report_hash, auditor, rationale, approved), queue shrinks; double decision → `AlreadyDecidedError`; unknown report/finding → `NotFoundError`-style ValueError/KeyError; empty rationale → ValueError.
- [ ] **Step 2:** Implement `pending_exceptions(report_hash)`, `record_decision(report_hash, finding_id, human_auditor_id, rationale, approved)`, `_decisions_index_path/_read_index/_write_index` (tmp + `os.replace`).
- [ ] **Step 3:** Run the test file — pass. Commit `feat(s34b): exception queue — pending view + signed WORM-logged decisions`.

### Task 4: endpoints + gateway proxy

**Files:** Modify `counterfactual_service/main.py`, `api_gateway/routers/counterfactual.py`. Test e2e in `tests/test_exception_queue.py`.

- [ ] **Step 1:** Failing e2e test: financial_audit → GET exceptions (n_pending == n flagged, evidence redacted) → POST decision → GET again (n_pending−1, n_decided+1); 409 on repeat; 404 unknown.
- [ ] **Step 2:** Implement `GET /audit/financial/{record_hash}/exceptions`, `POST /audit/financial/{record_hash}/exceptions/{finding_id}/decision` (pydantic `ExceptionDecisionRequest`: human_auditor_id, rationale min_length=1, approved) mapping module errors → 404/409; add proxies.
- [ ] **Step 3:** Run `tests/test_exception_queue.py tests/test_financial_audit.py tests/test_finance_pivot.py` + CI-exact ruff. Commit `feat(s34b): exception-queue endpoints + gateway proxy`.
