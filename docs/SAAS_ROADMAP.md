# AURA SaaS Roadmap — from demo to deployable multi-tenant product

**Goal (set 2026-06-15):** turn AURA from a scenario demo into a real,
immediately-deployable SaaS that serves **everyone, kids to experts**, with
**login + multi-tenant isolation + billing**, deployable to **Kubernetes via
the existing Helm chart**. One developer drives the build and pushes working
increments; the other picks up from the remote.

This document is the source of truth for that program. It is grounded in what
the codebase *actually* has today (verified 2026-06-15), so each phase closes a
real gap rather than rebuilding what exists.

---

## 1. Product principle — progressive disclosure ("for everyone")

AURA's depth (causal inference, PCAOB audit, self-healing pipelines) is expert
software. The way to serve a kid *and* an expert with one product is to layer
the surface, not dumb it down:

| Level | Who | What they see |
|-------|-----|---------------|
| **Simple** (default) | anyone | One action: *"Drop your data or ask a question."* → a plain-English answer ("No bias detected" / "This looks biased — here's why") + a shareable signed certificate. No jargon, no config. |
| **Pro** (one click deeper) | analysts | The dashboard: chat→SQL, audits, pipelines, history. Today's `/app`. |
| **Expert** (one more click) | auditors / data scientists | Estimator tables, refutations, E-values, signed artifacts, the SDK. Today's auditor views. |

A new user lands in **Simple** and reaches their first real result in **under
60 seconds**. "More detail" is always one click away, never in the face.

---

## 2. What already exists (don't rebuild)

- **Auth core**: `/api/v1/auth/register` (hashed password) and
  `/api/v1/auth/token` (open + password modes) in
  `api_gateway/routers/auth.py`; `shared/password.py`
  (`hash_password`/`verify_password`); `shared/auth.py`
  (`create_access_token`, `require_user`); `User` model in
  `metadata_store/models.py`. Production gates already reject open mode.
- **Tenancy primitive**: `workspace_id` scopes every gateway table
  (`api_gateway/persistence.py`).
- **Signed audit engine + "audit your own data"** (`POST /counterfactual/audit`)
  — already produces a plain-English verdict + signed, verifiable certificate.
  This is the natural core of the Simple level.
- **Deploy**: Helm chart at `deploy/helm/aura/` with armed production gates;
  multi-tier Docker images pushed to GHCR by CD.
- **Frontend**: React SPA with the `/app` dashboard (Pro) and the public audit
  service (the certificate flow). S37 "Terminal Authority" redesign in flight.

## 3. Gaps to close

1. **Tenancy is identity-detached** — `workspace_id` comes from a client
   header, not the JWT. Any token holder can read any workspace's data. *Must
   fix before this is a real SaaS.*
2. **No frontend auth** — no signup/login/logout/session/protected routes; the
   UI runs in open/demo mode.
3. **No onboarding / Simple surface** — no 60-second guided first-result flow.
4. **No billing** — no plans, checkout, metering, or plan gating.
5. **Deploy not turnkey** — Helm exists but needs the new services, secrets,
   ingress, and a managed Postgres story + operator runbook.

---

## 4. Phased plan

Each phase is a shippable, CI-green increment. Order minimizes wasted work:
identity-bound tenancy underpins billing and per-user data; the Simple surface
needs auth; billing needs accounts.

### Phase 1 — Identity-bound multi-tenancy (security foundation) · BACKEND
Make the tenant a property of *who you are*, not *what header you send*.

- Add an `org_id` (tenant) to `User`; every user belongs to exactly one org
  (auto-created at registration; invites later).
- Derive the effective tenant from the JWT in `require_user` and expose it as a
  request-scoped value; **ignore client workspace headers for data access**
  (keep workspace as a *within-tenant* folder concept, validated to belong to
  the caller's org).
- Enforce `org_id` filtering in every `persistence.py` read/write path; add a
  regression test that user A cannot read user B's saved query / file / history
  by any header.
- Migration for existing rows → a `default` org.
- **Acceptance:** a test proving cross-tenant reads 404/403; CI green.

### Phase 2 — Frontend auth + the Simple surface · FULL-STACK
- Signup / login / logout pages; token stored + refreshed; protected routes;
  "who am I" bootstrap on load.
- **Simple home**: one drop-zone / one question box → calls the existing audit
  (or ask-data) path → plain-English result + signed cert, with a "see the
  details" expander into Pro/Expert.
- First-run onboarding: pick "I want to check my data for bias / fraud / just
  explore" → sample dataset offered → first signed result in <60s.
- **Acceptance:** a brand-new account reaches a signed result without docs;
  Vitest + a Playwright happy-path.

### Phase 3 — Billing · FULL-STACK
- Stripe: products/plans (Free / Pro / Enterprise), Checkout, customer portal,
  webhooks (`checkout.session.completed`, subscription updates) → set the org's
  plan.
- Usage metering (audits/month, rows) + plan gating middleware (402 + upgrade
  prompt past limits).
- **Acceptance:** signup → free tier → upgrade via Checkout → plan reflected →
  gate enforced. Webhook signature verified.

### Phase 4 — Turnkey Kubernetes deploy · OPS
- Helm: add any new services (billing webhook handler), secret wiring
  (`STRIPE_*`, DB URL, signing + PII keys), ingress + TLS, and a **managed
  Postgres** value (external DB, not in-cluster) for durability.
- One-command path: `helm install aura ./deploy/helm/aura -f my-values.yaml`
  with a documented minimal `my-values.yaml`; a `make deploy` convenience.
- Operator runbook in `ENTERPRISE.md`: keys, scaling, backups, the signing-key
  stability rule (persist a stable `AURA_SIGNING_PRIVATE_KEY_HEX`, loaded into
  the env — see the 2026-06-15 verify post-mortem).
- **Acceptance:** a clean cluster → working public URL with login, audit, and
  billing, from one values file.

---

## 5. Cross-cutting

- **Security:** every new endpoint goes through `create_service()` (rate limit,
  headers, auth). Tenant isolation is enforced in persistence, not routers.
  Stripe webhooks verify signatures. No secrets in git.
- **Honesty:** the Simple verdict must use the same significance-aware logic as
  the expert view — never claim "biased" on a CI that straddles zero.
- **Signing-key durability:** the deploy must use a stable signing key (the
  2026-06-15 incident: an ephemeral shell-exported key made every certificate
  unverifiable after restart). Persist it and load it into the process env.

---

**Status:** Phase 1 in progress (started 2026-06-15). Update this section as
phases land; one feature branch + PR per phase, claimed in `docs/SPRINTS.md`.
