# AURA — Current Status

**Last verified:** 2026-08-11 · branch `feature/workbench-native-panels` · **51 commits ahead of `main`**

Everything below was *checked*, not assumed. Where something is unverified, it says so.
That distinction is the point of this file.

---

## What AURA is

An **AI data team**: analyst, engineer, and scientist.

| Role | What it does | Where it lives |
|---|---|---|
| **Analyst** | Ask in plain English → SQL → executed → answer + chart + narrative | Ask AURA, Terminal, Dashboards, Library, Query History |
| **Engineer** | Move and shape data: connectors, ETL pipelines, streaming, scheduling | Connectors, Pipelines, Streaming, Scheduler, Files & Data, Lineage |
| **Scientist** | Causal inference — *why*, not just *what* | Counterfactuals (TMLE, DR-learner, IPW, PSM, double-ML, forest-DR, IV-2SLS), conformal intervals, E-values |

The **financial-audit service is one branch built on this platform**, not the platform itself.
It adds PCAOB-style checks and cryptographically signed, replayable audit certificates.

---

## Health right now

| Signal | State |
|---|---|
| Backend test suite | **2035 passed**, 36 skipped, 0 failed |
| Frontend test suite | **330 passed** across 74 files |
| npm vulnerabilities | **0** (all severities) |
| GitHub CI | 13/14 jobs green; the failing Backend Tests job was fixed 2026-08-11 |
| Type check / lint | `tsc --noEmit` clean, `eslint --max-warnings 0` clean, `ruff` clean |

---

## Enterprise hardening — roughly 70% toward the 80% bar

Scored by "would this stop an enterprise deployment?", not by lines changed.

### Done and verified

- **Multi-tenant isolation** on jobs, files, dashboards, saved queries, connections.
  Cross-tenant reads return **404, not 403**, so responses are not existence oracles.
  Job isolation was verified in a live browser, not only in tests.
- **Production guards fail closed.** `ENVIRONMENT=prod-eu` (or any unrecognised name) used to
  silently disable JWT auth and collapse every tenant into one shared workspace. Now unknown
  names are treated as production and the deploy refuses to boot.
- **The audit trail can no longer be silently off** in production (`AURA_AUDIT_ENABLED`).
- **External verification API reachable** — `/jwks`, RFC 6962 signed tree head, Merkle inclusion
  proofs, ledger proofs. An outside auditor can verify a certificate without trusting the server.
- **Credentials encrypted at rest** (Fernet), never logged, never returned on the wire.
- **Credential endpoints throttled** — login and registration were previously unlimited.
- **Money summed exactly** (Decimal) — materiality figures get signed, so float drift was attested as fact.
- **Dashboards persist**; they used to vanish on every restart.
- **Core analyst loop proven** — a GROUP BY was computed independently in Python from the raw
  CSV and matched AURA's answer exactly.

### Not done — the gap to 80%

| Gap | Impact | Size |
|---|---|---|
| **Connectors don't work end-to-end** | The *engineer* role can't query an external database. Gateway and connectors service hold separate stores. Needs an architecture decision (shared DB vs credentials over inter-service traffic vs callback). | Medium — **decision needed first** |
| **Data-movement layer unaudited** | Pipelines, streaming, upload→queryable, webhooks have never been assessed. Unknown whether any silently produces *wrong* data. | Medium |
| **Scheduler service unreachable** | Standalone distributed scheduler has no gateway route at all — only a `/health` ping. | Small–medium |
| **`semantic_builder` never written** | A live route imports it and always returns an error with HTTP 200. | Small |
| **PII masking on the wrong service** | Mounted only on `ingestion_service`, which never starts. Cannot be mounted wholesale on the gateway — it rewrites inbound bodies and would redact `email`, breaking login. Needs scoping to data paths. | Small–medium |
| **No correlation ID across service hops** | A single user action can't be traced across services. | Small |
| **No tenant-erasure path** | `DELETE /workspaces/{id}` only edits an in-memory list. Conflicts with the append-only ledger — needs a documented policy. | Decision needed |

---

## Deploy readiness

**Blocking deploy:** nothing, if the audit branch is the demo target.
**Blocking a customer using the engineer features:** connectors.

Required environment for a production boot (all now fail closed):

```
ENVIRONMENT=production
AURA_JWT_ENABLED=true
AURA_AUTH_MODE=password
AURA_AUDIT_ENABLED=true
SECRET_KEY=<strong>
AURA_CREDENTIAL_ENCRYPTION_KEY=<fernet key>
CORS_ALLOWED_ORIGINS=https://<your-domain>
```

Generate the credential key with:
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

---

## Honest unknowns

- Streaming and pipelines: **never verified end to end.**
- Observability: metrics exist and are real, but nothing observes the in-process audit engine
  blocking the event loop.
- The `/replay/bulk` residual: an authenticated auditor in tenant A can submit a tenant-B hash
  and learn whether it verifies.

---

## Suggested next three

1. **Decide the connectors architecture**, then implement it — unblocks the engineer role.
2. **Run the data-movement audit** — the largest unassessed area of core product.
3. **Deploy the current branch** to a staging environment and use it. Nothing on this list
   substitutes for seeing it run.
