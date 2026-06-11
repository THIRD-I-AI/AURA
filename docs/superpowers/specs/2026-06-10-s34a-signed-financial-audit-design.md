# S34a — Signed Financial Audit core (design)

**Status:** approved 2026-06-10 · Owner: Mounith · Branch: `feature/s34-finance-auditor-pivot`
**Anchor:** YC "AI-native service" thesis (deliver the signed audit *outcome*, not a tool) + PCAOB AS-2110/2305/2201/2401 (audit scopes) and AS-1215 (audit documentation / completion document).

## Problem

The S34 pivot shipped scaffolding: a `FinancialAuditorAgent` with PCAOB-mapped checks, an ED25519 `cryptography.py` (in-memory keystore — keys vanish on restart, unverifiable signatures), a perimeter PII redactor that blinds the very fraud checks the pivot advertises, and a `/jwks` endpoint that is inert because nothing generates a key. None of it is wired into a path that produces the sellable outcome: **a signed, independently-verifiable financial-audit report.**

This slice (S34a) ties the pieces into that outcome and fixes the gaps by **reusing existing AURA primitives** rather than the parallel reinventions:
- `counterfactual_service/signing.py` — **persistent** ED25519 (`sign_bytes`/`verify_bytes`, key auto-persisted at `data/keys/signing_ed25519.pem`, env-overridable). The audit engine already signs every artifact with it.
- `shared/audit_log.py` — immutable SHA-256 **hash-chained WORM log** (`append`, `verify_chain`) + `audit_human_override` (AS-1215 contradiction logging, already added).
- `counterfactual_service/persistence.py` — artifact write/read by hash (drives the existing `/verify/{hash}`).

## Goal

`POST /audit/financial` accepts a ledger batch (+ optional supporting documents), runs the PCAOB checks on the **raw** data, assembles an **Engagement Completion Document** (AS-1215 §.12), **signs** it with the persistent ED25519 key, persists it, appends an immutable record to the hash-chained audit log, and returns a signed report with an independent verify URL. PII is redacted only on the egress/display projection, never in the signed artifact.

## Locked decisions

1. **Signing** → reconcile onto `signing.py` (persistent key; survives restarts). The in-memory keystore is removed.
2. **Invocation** → synchronous `POST /audit/financial` endpoint (demonstrable, testable; the YC demo is one call).
3. **PII** → keep raw in-boundary (fraud checks need employee/vendor data for AS-2410 related-party detection); redact only at egress/display.
4. **`FinancialAuditorAgent` stays a service class** (not a `BaseAgent`) — the sync endpoint calls it directly; orchestrator-tool wiring is deferred (YAGNI).

## Architecture

Small, focused units; reuse the engine's signing/persist/verify path.

### 1. Reconcile `counterfactual_service/cryptography.py` onto `signing.py`
- Add `signing.public_key_raw_b64url()` (load the persistent key → raw 32 bytes → base64url, no padding) for JWK `x`.
- `get_jwks()` returns a **single** JWK built from `signing.py`'s public key: `{kty:"OKP", crv:"Ed25519", kid, x, revoked}`. `kid` is a stable tag, e.g. `"aura-ed25519-" + signing.signing_key_source()`.
- Revocation persists to `data/keys/revoked_kids.json` (small JSON set); `is_revoked(kid)` reads it; the JWK `revoked` flag and any future signing guard consult it.
- Remove the in-memory `_key_store`/`generate_agent_keypair`/`sign_payload` parallel path (or reduce `sign_payload` to delegate to `signing.sign_bytes`). `soft_revoke_key` writes the persisted set.

### 2. `FinancialAuditorAgent.run_full_audit(...)` (in `agents/specialists/financial_auditor.py`)
```python
async def run_full_audit(self, ledger, purchase_orders, invoices, journal_entries,
                         historical_reports=None) -> list[AuditFinding]:
    findings = []
    await self.execute_as2110_risk_assessment(historical_reports or [])  # sets materiality, logs
    findings += await self.execute_as2305_analytical_procedures(ledger)
    findings += await self.execute_as2201_internal_controls(purchase_orders, invoices)
    findings += await self.execute_as2401_fraud_detection(journal_entries)
    return findings
```
Runs on **raw** data so AS-2401/2410 cross-checks see employee/vendor fields.

### 3. `counterfactual_service/financial_report.py`
- `dataset_fingerprint(ledger, pos, invoices, jes) -> str` — SHA-256 over canonical-JSON of the full inputs = AS-1215 §.10 "100% population" proof.
- `build_completion_document(tenant_id, findings, fingerprint, materiality) -> dict` — AS-1215 §.12 shape: `tenant_id`, `dataset_fingerprint`, `materiality_threshold`, `findings` (list of `AuditFinding.model_dump()`), `risk_counts` (by level), `performed_by` ({agent, version} — §.06 "who performed the work"), `generated_at`. Numbers canonicalised for byte-stable hashing.
- `sign_and_persist(doc) -> SignedReport` — `canonical = strip+json(doc)`; `record_hash = sha256(canonical)`; `signature_b64 = signing.sign_bytes(canonical)`; `persistence.write_artifact(record_hash, {**doc, signature...})`; `audit_log.append("financial_audit_completed", {record_hash, signature_b64, tenant_id, dataset_fingerprint, n_findings})`. Returns `{document, record_hash, signature_b64, signature_status, signing_key_source}`.
- `client_view(report) -> dict` — egress projection: `redact_pii` applied to a deep copy (findings' `evidence_payload` PII masked). The signed/persisted artifact is untouched.
- `verify_report(record_hash) -> dict` — read artifact → recompute canonical (excluding signature/hash) → `signing.verify_bytes` → `{verified, signature_status, record_hash}`. Mirrors the counterfactual `/verify`.

### 4. Endpoints — in `counterfactual_service/main.py` (the service mounts routes inline; there is no `routers/` subdir — that is a gateway pattern). Pure logic lives in `financial_report.py`; `main.py` only wires the two routes.
- `POST /audit/financial` body `FinancialAuditRequest{tenant_id, ledger[], purchase_orders[], invoices[], journal_entries[], historical_reports?}` → run_full_audit → build doc → sign_and_persist → return `client_view(report)` (redacted evidence) **plus** `record_hash`/`signature_b64`/`verify_url`.
- `GET /audit/financial/verify/{record_hash}` → `verify_report`.
- Gateway-proxied under `/api/v1/...` following the existing counterfactual proxy pattern in `api_gateway/routers/counterfactual.py`.

## Data flow

```
POST /audit/financial {tenant_id, ledger[], purchase_orders[], invoices[], journal_entries[]}
 → run_full_audit (RAW)                       AS 2110/2305/2201/2401
 → dataset_fingerprint (SHA-256, 100% pop.)
 → build_completion_document                  AS-1215 §.12
 → signing.sign_bytes(canonical)              persistent ED25519
 → persistence.write_artifact(hash, doc+sig)
 → audit_log.append("financial_audit_completed", …)   immutable hash chain
 → return client_view(report)                 PII redacted at egress only
GET /audit/financial/verify/{hash}
 → read artifact → recompute → signing.verify_bytes → {verified}
```

## Error handling

- No signing key available (`signing.sign_bytes` → None): return the report with `signature_status="unsigned"` and a warning; never 500. (Matches the engine's posture.)
- A revoked active kid: `sign_and_persist` refuses and returns `signature_status="unsigned"` with reason.
- Empty/malformed ledger: pydantic 422 at the boundary; missing optional doc lists default to `[]`.
- A failing individual check must not abort the others (each PCAOB method is independent; collect what succeeds).

## Testing (Tier A — pure, no optional deps)

- `run_full_audit` over a crafted batch (a >$100k ledger variance + an invoice with no matching PO + a round-dollar JE + a duplicate JE) yields findings spanning AS-2305 / AS-2201 / AS-2401(round + duplicate); a clean batch yields none.
- `dataset_fingerprint` deterministic + order-sensitive where it should be; `build_completion_document` has all AS-1215 fields + correct `risk_counts`.
- `sign_and_persist` → `verify_report` roundtrips `verified=True`; tampering the persisted doc → `verified=False`.
- `client_view` redacts `employee_name`/`email` in findings evidence **while** the persisted artifact retains them and still verifies (proves "raw in-boundary, redact at egress").
- `get_jwks` returns exactly one JWK whose `x` matches `signing.py`'s public key; after `soft_revoke_key`, the JWK shows `revoked: true` and the kid is persisted.
- Endpoint: `POST /audit/financial` returns a signed report with redacted evidence; `GET …/verify/{hash}` confirms `verified: True`. (TestClient; gated/skipped if heavy deps unavailable.)

All Tier A — pandas/cryptography are base deps, so this runs on the base backend lane. New `tests_contract/` already exists; the financial tests live in `tests/` (base lane). A CI lane for `tests_contract/` is S34c scope.

## Security

- Signing key is the persistent `signing.py` key (env hex / PEM path / persisted file) — never the removed in-memory store. Public key is exposed via `/jwks` (public by design — verifiers fetch it); the mutation endpoint `/revoke-key` stays admin-gated (S34 hardening).
- The signed artifact contains raw evidence (compliance requires it; the WORM log is the system of record). Redaction is a non-authoritative display transform; it must never feed back into the hash/signature.

## Non-goals (later sub-projects)

- S34b HITL UI (surface findings + capture override → `audit_human_override`).
- S34c Kafka/ingestion wiring + `on_event`→lifespan + Kafka-fails-boot + `tests_contract/` CI lane.
- S34d tokenization (vs the egress-redaction MVP).
- `FinancialAuditorAgent` as a `BaseAgent` / orchestrator tool; multi-key rotation.
