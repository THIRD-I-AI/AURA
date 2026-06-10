# S34b — HITL Exception Queue (backend) — Design

**Sprint:** S34b (second sub-sprint of the S34 finance-auditor pivot; issue #60, branch `feature/s34-finance-auditor-pivot`)
**Date:** 2026-06-10
**Depends on:** S34a signed financial audit core (`financial_report.py`, `sign_and_persist`, `verify_report`, `client_view`)

## Problem

S34a produces a signed AS-1215 Engagement Completion Document whose findings
carry `requires_human_review: true` — but nothing consumes that flag. The
HITL workbench (`frontend/src/components/HITL/ExceptionQueue.tsx`) is a mock:
hard-coded exceptions and a `console.log` where the backend call should be.
PCAOB AS 1215 requires documenting the human auditor's agreement or
contradiction with each AI finding. `shared/audit_log.py` already ships
`audit_human_override(ai_record_hash, human_auditor_id, rationale, approved)`
— it has zero callers.

## Goal

A reviewable per-report exception queue plus a decision endpoint that makes
each human approve/override (a) immutable in the WORM audit chain, (b) a
signed, content-addressed, independently-verifiable artifact, and (c) visibly
shrinks the pending queue.

## Design

### 1. Stable finding identity (`finding_id`)

`build_completion_document` assigns each finding
`finding_id = sha256(canonical_dumps({"i": index, "finding": finding}))`
**before** signing, so IDs live under the report signature (tamper-evident)
and index-salting keeps two content-identical findings distinct.
`FINANCIAL_AUDITOR_VERSION` bumps to `0.2.0` (document format change —
AS-1215 §.06 provenance must record which format generated the document).

### 2. Decision records are signed artifacts

New `counterfactual_service/exception_queue.py`:

* `_sign_document(doc)` is factored OUT of `financial_report.sign_and_persist`
  (hash → ED25519 sign unless revoked → persist canonical JSON).
  `sign_and_persist` keeps its report-specific `audit_event`; the decision
  path pairs `_sign_document` with the WORM `audit_human_override` call.
* A decision document:
  `{document_type: "HumanOverrideRecord", pcaob_standard: "AS 1215",
    report_record_hash, finding_id, human_auditor_id, rationale, approved,
    decided_at}` — content-addressed by its own `record_hash`, verifiable
  through the existing generic `verify_report` (it recomputes
  `_signable(...)` of ANY persisted doc).

### 3. Queue state

Ground truth = signed decision artifacts + WORM log. For O(1) reads, a
sidecar index `<report_hash>.decisions.json` in the artifact dir maps
`finding_id → decision_record_hash` (atomic tmp+`os.replace` rewrite, same
pattern as `persistence.write_artifact`). The index is a convenience view —
losing it loses no evidence.

* `pending_exceptions(report_record_hash)` → findings with
  `requires_human_review` and no decision, **PII-redacted at egress**
  (same `redact_pii` policy as `client_view`; the signed report keeps raw).
* `record_decision(report_record_hash, finding_id, auditor, rationale,
  approved)` → validates report + finding exist, rejects double-decisions
  (conflict), signs + persists the decision, appends `audit_human_override`,
  updates the index.

### 4. Endpoints

In `counterfactual_service/main.py` (+ identical gateway proxy routes in
`api_gateway/routers/counterfactual.py`):

* `GET /audit/financial/{record_hash}/exceptions`
  → `{record_hash, pending, n_pending, n_decided}`; 404 unknown report.
* `POST /audit/financial/{record_hash}/exceptions/{finding_id}/decision`
  body `{human_auditor_id, rationale, approved}`
  → stored decision record (with `verify_url`); 404 unknown report/finding,
  409 already decided.

Auth posture matches S34a's `/audit/financial` (gateway-level middleware;
no service-level role gate). Revisit with S34d/PII tokenization.

### 5. Out of scope

Frontend wiring of `ExceptionQueue.tsx` (Rohith's track — the mock's
`handleOverride` maps 1:1 onto the POST above), cross-report queue listing,
auditor RBAC, decision amendment (the WORM stance: decisions are final;
a wrong decision is corrected by a new audit run).

## Error handling

Unknown report/finding → 404; double decision → 409; revoked signing key →
decision persists with `signature_status: "unsigned"` (same honest-degradation
contract as S34a reports). Empty rationale rejected (422) — AS 1215 requires
the contradiction *rationale*, not just the verdict bit.

## Testing (Tier A, monkeypatched persistence/audit like S34a)

finding_id determinism/uniqueness/signature-coverage; pending lists only
unreviewed findings with redacted evidence; decision → WORM call args +
signed artifact verifies + queue shrinks; double-decision conflict; unknown
ids; endpoint e2e run-audit → list → decide → list.
