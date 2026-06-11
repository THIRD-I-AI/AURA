# S34d — PII Tokenization at Egress — Design

**Sprint:** S34d (last sub-sprint of S34; issue #66, branch `feature/s34d-pii-tokenization`)
**Date:** 2026-06-11

## Problem

Egress masking is a blanket `[REDACTED]` (`shared/pii_masking.redact_pii`).
An auditor reviewing the exception queue cannot tell whether three flagged
invoices involve the *same* employee or three different ones — exactly the
correlation that matters for AS-2401 fraud patterns (duplicate payments to
one person) — without being shown raw PII.

## Design

### Deterministic keyed pseudonymization

`PII-` + first 12 hex of `HMAC-SHA256(key, tenant_id|field|value)`:

* **Same (tenant, field, value) → same token** — correlation works across
  findings, reports, and the exception queue.
* **HMAC-keyed, not plain-hashed** — names/SSNs are low-entropy; an unkeyed
  deterministic hash is dictionary-invertible. Key comes from
  `AURA_PII_TOKEN_KEY` (deployment secret, independent of the ED25519
  signing key — no cross-purpose key reuse).
* **Tenant-salted** — token equality never leaks across tenants.
* **Field-salted** — `employee_name` and `email` of the same person don't
  produce linkable tokens (privacy-positive default; revisit if auditors
  need cross-field entity resolution).

### Fail-safe mode selection

New `mask_pii_egress(data, *, context)` in `shared/pii_masking.py`:
tokenizes when `AURA_PII_TOKEN_KEY` is set, otherwise falls back to the
existing `[REDACTED]` behavior. **No key → no deterministic output, ever.**
`financial_report.client_view` and `exception_queue.pending_exceptions`
switch to it, passing `tenant_id` as context. Non-string values under PII
keys are tokenized over `str(value)`.

### Unchanged

* `redact_pii` keeps its exact current behavior (middleware + fallback).
* `PIIMaskingMiddleware` (ingestion perimeter) is untouched. **Known
  tension, out of scope:** it permanently redacts inbound ERP payloads,
  which conflicts with the S34a "raw in-boundary, redact at egress" policy
  for fraud checks on ERP-ingested ledgers; that's an ingestion-pipeline
  decision for a future sprint, recorded here so it isn't lost.
* The signed artifact keeps raw evidence; tokens never feed the
  hash/signature (same invariant as S34a redaction).

## Testing (Tier A)

Token determinism (same triple → equal; value/tenant/field changes →
distinct); format `PII-[0-9a-f]{12}`; raw value absent from output; no key
→ `[REDACTED]`; recursion/non-PII preservation; e2e: two findings sharing
an employee produce equal tokens in `client_view` AND the exception queue,
while the stored artifact keeps raw and still verifies.
