# AURA — Investor Demo Walkthrough

A 10-minute live demonstration of the working product. Everything below
runs against real services — no mocked data paths.

## Boot (local, one terminal each)

```sh
# Backend gateway (mounts all services in-process)
cd aurabackend
python -m uvicorn api_gateway.main:app --port 8000

# Frontend
cd frontend
npm run dev          # http://localhost:5173
```

Optional but recommended for the full PII story: set `AURA_PII_TOKEN_KEY`
(any long random string) in the backend environment before boot — audit
findings then show correlatable `PII-xxxxxxxxxxxx` tokens instead of
blanket `[REDACTED]`.

Full-stack alternative: `docker compose up` (per-service containers,
Kafka, Jaeger) or the Helm chart for Kubernetes (production posture by
default — see ENTERPRISE.md → Production Deployment Checklist).

## The 4-act demo

### Act 1 — Ask the data a question (Chat)
Upload a CSV on **Files & Data**, then ask a question in **Chat**. Behind
the answer: LLM-generated SQL is **cross-checked against an independently
generated pandas solution** (S32 DPC) — hallucinated SQL that "looks right"
gets caught and retried before the user ever sees it.

### Act 2 — Causal audit, signed (Counterfactual)
Open **Counterfactual** → run a demo scenario (e.g. *Fair Lending* on the
officer-leniency instrument, or *COMPAS Recidivism* on the real ProPublica
data). The result is not a chatbot answer: it's a multi-estimator causal
analysis (DoWhy/EconML: DML, TMLE, IV) with refutation tests, a
significance-honest verdict, and an **ED25519-signed, hash-sealed audit
artifact** — click *verify* to recompute the signature live, download the
signed PDF.

### Act 3 — The AI auditor with a human in the loop (Audit Workbench)
Open **Audit Workbench** → *Run sample financial audit*. AURA's PCAOB-mapped
auditor (AS 2110/2305/2201/2401) scans a ledger batch and produces a signed
**AS 1215 Engagement Completion Document** over a fingerprint of 100% of the
population — not a sample. Findings that require human judgment land in the
exception queue:

1. PII appears as deterministic tokens — the auditor can see that *the same
   employee* is behind three flagged invoices without seeing who it is.
2. Pick a finding, write a rationale (mandatory — AS 1215), Approve or
   Override.
3. The decision becomes its own **signed HumanOverrideRecord**, chained into
   the WORM audit log, attributed to the auditor's verified JWT identity —
   not a self-asserted name. The queue shrinks live; the verify badge stays
   green.

### Act 4 — The trust story (one slide of facts)
- Every artifact (analysis, audit report, human decision) is content-
  addressed, ED25519-signed, and independently re-verifiable by a third
  party against the published JWKS.
- The audit log is an append-only SHA-256 hash chain (TRAIGA), shippable to
  S3 Object Lock for physical immutability.
- Production boot **fails closed**: open auth mode, default JWT secrets, and
  wildcard CORS are rejected at startup, ERP ingestion is 401 fail-closed,
  and the LLM-code sandbox is AST-allowlisted.
- CI: ~30 checks per PR across 2 Python versions — unit, causal, streaming,
  contract (Schemathesis), SDK-drift, CodeQL, bandit. 0 open Dependabot
  alerts.

## If asked "is this real or a demo?"

The demo path and the production path are the same code. The only
demo-specific affordances are the canned sample batch in the Audit
Workbench and the open-mode token minting — both clearly scoped to
non-production environments (open mode cannot boot in production).
