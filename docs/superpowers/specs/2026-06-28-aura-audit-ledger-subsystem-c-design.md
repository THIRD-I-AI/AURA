# Subsystem C — Durable Audit Ledger (the trust layer)

**Status:** Detailed design. Parent: [AURA Commander Platform umbrella](./2026-06-24-aura-commander-platform-umbrella-design.md), Subsystem C.
**Date:** 2026-06-28
**Author:** Mounith + Claude (Opus 4.8)
**Product framing:** opening wedge = **fair-lending compliance SaaS**. This ledger is the product's defensibility moat, not an internal log — it is the artifact a CFPB/DOJ exam, an internal auditor, or a plaintiff's expert reviews. Design it to survive adversarial review.

---

## Why this is the product, not a feature

AURA's value to a regulated buyer is a signed answer to "did a protected attribute *causally* drive this automated decision, after adjusting for legitimate factors?" The buyer pays not for the chart but for an artifact that **cannot be quietly edited after the fact** — so when the regulator asks, they hand over a certificate plus a tamper-evident chain proving the *sequence* of audits wasn't altered, nothing was deleted, and each was signed off by an accountable human. The cryptographic ledger is what separates "we ran some pandas" from "here is court-defensible evidence." Getting it *wrong* (a chain that looks tamper-evident but isn't) is worse than not having it, which is why this gets a real design.

---

## The validated gap (current code, 2026-06-28)

- **Off by default, at import time.** `shared/audit_log.py:49` reads `AUDIT_ENABLED = os.getenv("AURA_AUDIT_ENABLED","false")=="true"` *once at import*; `append()` early-returns when false (`:110`). So `financial_report.sign_and_persist` faithfully calls `audit_event("financial_audit_completed", …)` (`financial_report.py:100`) and on a default deploy **it does nothing**. The signature is real; the chain is dead.
- **Single-process, local-file chain.** `_AuditWriter` serialises with an in-process `threading.Lock` and writes `audit-<service>-<day>.jsonl` keyed only on service+day. Two replicas on a shared volume corrupt each other's chain; on separate volumes it forks. Not on a durable store.
- **Local-file artifact store.** `counterfactual_service/persistence.py` writes signed certs to `<record_hash>.json` on local disk (`/var/log/aura/artifacts`). Content-addressed (so writes are idempotent) but not durable/replicated.
- **Record is missing audit-grade identity.** `build_completion_document` has `tenant_id`, `dataset_fingerprint`, `findings[].finding_id`, `performed_by`, `generated_at` — but **no `subject_id`** (can't reconstruct "every audit for this applicant/model/cohort") and **no preparer/reviewer assignment** (PCAOB AS 1215 §.6 requires both, with sign-off).
- **Fingerprint is incomplete.** `dataset_fingerprint()` hashes only `ledger, purchase_orders, invoices, journal_entries` — `goods_receipts`, `historical_reports`, `period_end` drive findings but aren't bound by the proof.
- **Merkle machinery is correct but local.** `daily_merkle_root` / `inclusion_proof_for_record` are fully coded (RFC 6962 leaf prefix and all) but read the local JSONL.

---

## Scope (ledger-core-first)

This spec builds the **durable, tamper-evident, tenant-scoped ledger core** and the identity it must carry. The document-extraction / evidence-intake pipeline (turning a PDF invoice or a loan file into structured evidence with chain-of-custody) is **a separate later subsystem** — large enough to deserve its own design; it will append `evidence_ingested` records to *this* ledger when it lands.

In scope:
1. A durable Postgres-backed append-only ledger, multi-replica-safe, tenant-scoped, with a per-tenant hash chain.
2. **Always-on** for the audit-completion path (decoupled from the LLM-prompt-audit flag).
3. Audit-grade record identity: `subject_id` (+ `subject_type`), assignment (`preparer_id`, `reviewer_id`, `decided_at`), full-input fingerprint, reference to the signed cert.
4. The existing Merkle root + inclusion proofs re-pointed at the durable store.
5. Verify surface: chain verification, inclusion proof, and **subject audit history**.

Out of scope (deferred, noted as dependencies): document extraction & evidence custody; moving the *artifact body* store off local files (the chain references the cert by `record_hash`; durable artifact blobs are a fast-follow); BISG/proxy protected-class inference.

---

## Architecture

```
 financial_report.sign_and_persist / counterfactual cert path
        │  (always-on)
        ▼
 shared/audit_ledger.py  append(record)         ◀── the one write path
        │   per-tenant serialised hash chain
        ▼
 Postgres: audit_ledger table (tenant_id, seq, prev_hash, record_hash,
           subject_id, subject_type, preparer_id, reviewer_id, decided_at,
           cert_hash, input_fingerprint, kind, payload_json, ts)
        ▲
        │  reads
 verify surface (counterfactual_service / gateway):
   GET /audit/ledger/verify?tenant&day        → chain walk report
   GET /audit/ledger/proof/{record_hash}       → Merkle inclusion proof + root
   GET /audit/ledger/subject/{subject_id}      → ordered audit history for a subject
```

The ledger lives in a new `shared/audit_ledger.py` backed by a durable SQLAlchemy store (mirroring the S50 `persistence.py` `session_scope()` + lazy-init pattern, so tests that import it without the lifespan still get tables). It is a distinct module from the legacy `shared/audit_log.py` (JSONL prompt log) — that stays for LLM-prompt traceability; the *audit-completion ledger* is this new durable store.

---

## The hard part: multi-replica hash chaining

A hash chain needs a **total order per chain**, but concurrent replicas don't have one for free. Per-tenant chains + DB serialization:

- Each tenant has an independent chain. `seq` is a per-tenant monotonic integer; `prev_hash` is the previous record's `record_hash` *within that tenant*.
- **Serialize appends per tenant** with a Postgres transaction-scoped advisory lock keyed on the tenant: `pg_advisory_xact_lock(hashtext(tenant_id))`. Within the lock: read the tenant's tip (`MAX(seq)` row), compute `prev_hash = tip.record_hash`, `seq = tip.seq + 1`, insert, commit (lock releases on commit).
- **Correctness net under any race:** a `UNIQUE (tenant_id, seq)` constraint. If two replicas ever interleave (advisory lock bug, split brain), the second insert violates the constraint and retries from a fresh tip read rather than silently forking the chain. Fail-closed.
- `record_hash = sha256(canonical_json(stable_fields_including_prev_hash_and_seq))` — same construction as today's `_AuditWriter`, extended with `seq` and the new identity fields so reordering is detectable.
- SQLite (dev/single-node) has no advisory locks → fall back to a table-level write serialization (a single-writer lock or `BEGIN IMMEDIATE`); the `UNIQUE(tenant_id, seq)` constraint carries correctness either way. The advisory-lock path is the production (Postgres) path.

This is a standard DB-serialized hash chain; the novelty is only that we make it explicit and tenant-scoped.

---

## Record schema

```python
@dataclass
class LedgerRecord:
    tenant_id: str
    seq: int                       # per-tenant, monotonic, gap-free
    kind: str                      # "fairness_audit_completed" | "financial_audit_completed" | "human_override" | ...
    subject_id: str                # tenant-scoped opaque id of the audited entity
    subject_type: str              # "model" | "decision_cohort" | "applicant" | "dataset"
    preparer_id: str               # who ran/owns the audit (AS 1215 §.6)
    reviewer_id: Optional[str]     # who signed off; None until reviewed
    decided_at: Optional[str]      # ISO ts of human sign-off
    cert_hash: str                 # record_hash of the signed certificate artifact
    input_fingerprint: str         # sha256 over canonical-JSON of ALL audited inputs
    payload: Dict[str, Any]        # compact, truncated metadata (verdict summary, n_findings, materiality, signature_status)
    prev_hash: str
    record_hash: str               # sha256 of the stable fields above
    ts: str                        # ISO append time
```

`subject_id` is the answer to "multiple audits for the same person/model": every audit carries it, so the chain reconstructs a subject's full history and supersession lineage. `preparer_id`/`reviewer_id` is the assignment/segregation-of-duties answer. `input_fingerprint` is the fixed, complete proof-of-inputs.

---

## Changes to existing code

- `shared/audit_ledger.py` (**new**): the durable store + `append_audit(record) -> LedgerRecord`, `verify_chain(tenant, day)`, `merkle_root(tenant, day)`, `inclusion_proof(tenant, cert_hash)`, `subject_history(tenant, subject_id)`. Reuses `shared/merkle.py` (RFC 6962) unchanged.
- `counterfactual_service/financial_report.py`: `build_completion_document` gains `subject_id`, `subject_type`, `preparer_id` (params, threaded from the request); `dataset_fingerprint()` extended to cover **all** inputs (`+ goods_receipts, historical_reports, period_end`); `sign_and_persist` calls the new `append_audit(...)` **always-on**, not the flag-gated `audit_event`.
- The causal-fairness cert path (the lending/COMPAS scenarios in `counterfactual_service`) calls the same `append_audit(...)` so fairness audits chain identically.
- `shared/config.py`: a dedicated `audit_ledger_enabled` defaulting **true** (the ledger is the product) — distinct from the LLM-prompt `AURA_AUDIT_ENABLED`. A production guard fails loud if a prod deploy disables it.
- Verify endpoints added (service TBD in plan — counterfactual_service owns signing, so likely there, proxied by the gateway).

The legacy `shared/audit_log.py` JSONL chain stays for LLM-prompt traceability and is unchanged; `verify_chain` there still works for old logs.

---

## End-to-end flow (lending)

1. A fair-lending audit runs (causal effect of the protected attribute on approval, IV-identified) → produces findings + a verdict.
2. `sign_and_persist`: Ed25519-signs the certificate (unchanged), computes the **complete** input fingerprint, then `append_audit(...)` writes a durable, per-tenant-chained record carrying `subject_id` (the model/cohort/applicant), `preparer_id`, and the `cert_hash`.
3. A human fair-lending officer reviews in the HITL workbench → `append_audit(kind="human_override"|"review_signoff", reviewer_id, decided_at, references the cert_hash)` chains the sign-off.
4. At exam time: `GET /audit/ledger/subject/{model_id}` returns the full, ordered, hash-verified history; `GET /audit/ledger/proof/{cert_hash}` returns a Merkle inclusion proof against the day's root — independently verifiable without trusting AURA.

---

## Testing

- **Tenant isolation:** records under tenant A never appear in tenant B's chain/history/proofs.
- **Chain integrity:** append N records, `verify_chain` ok; mutate one row's payload in the DB → `verify_chain` flags the exact seq (record_hash mismatch) and every successor (prev_hash mismatch).
- **Concurrency (the load-bearing test):** many concurrent `append_audit` for one tenant (threads/processes) → resulting chain is gap-free, strictly ordered, and `verify_chain` ok; assert no two records share a `seq` (the UNIQUE constraint held).
- **Always-on:** with `AURA_AUDIT_ENABLED` unset/false (the legacy prompt-log flag), an audit completion **still** appends to the ledger (decoupled).
- **Identity:** `subject_history` returns a subject's audits in order; a record missing `preparer_id` is rejected at the boundary.
- **Fingerprint completeness:** changing `goods_receipts`/`historical_reports`/`period_end` changes `input_fingerprint` (regression against the current 4-input gap).
- **Merkle:** inclusion proof for a record verifies against the day's root via `shared/merkle.verify_inclusion`; a forged record_hash fails.
- Tier B (Postgres) lane for the advisory-lock concurrency path; Tier A (SQLite) for the rest.

---

## Risks & boundaries

- **Honest scope:** these primitives make AURA's output *defensible*; they do not make it a statutory audit — a fair-lending exam still involves the institution's compliance function and counsel. The product sells *defensibility and protection*, framed as "hand the regulator a signed certificate," not "find your violations."
- **Artifact durability** is a deliberate deferral: the chain is durable now; the signed cert *body* still lives in content-addressed local files. A multi-replica deploy needs those on shared/durable blob storage too — tracked as the immediate fast-follow (the chain already commits to them by `cert_hash`, so moving the bytes later doesn't break verification).
- **Protected-attribute data** (race/ethnicity in lending) is legally sensitive and often unavailable directly (BISG proxy territory). Out of scope here, but the ledger must never store raw protected attributes in `payload` — only fingerprints and verdict summaries.
- **Backfill:** existing local-JSONL chains are not migrated; the durable ledger starts fresh. Old logs remain verifiable via the legacy verifier. Noted for the plan.

---

## What gets a plan next

The implementation plan covers, in order: (1) `shared/audit_ledger.py` durable store + per-tenant chained `append_audit` with the advisory-lock + UNIQUE serialization and its concurrency test; (2) the complete `input_fingerprint` + the record identity fields threaded through `build_completion_document`; (3) always-on wiring of `sign_and_persist` + the fairness-cert path onto `append_audit`; (4) Merkle/inclusion/subject-history re-pointed at the durable store; (5) the verify endpoints + gateway proxy.
